"""
Tests for the marketing-preferences email suppression in ``_schedule_send``.

Marketing schedule emails (recurring nudge, upgrade reminder) pass
``is_marketing=True`` and must be suppressed unless the recipient's
``UserProfile.meta['marketing_preferences']`` contains ``'Futures eLearning'``.
Transactional emails (course updates) pass ``is_marketing=False`` and are always
sent.  See ``openedx/core/djangoapps/schedules/tasks.py::_schedule_send`` (added
in commit cdd0ce984d).
"""


from unittest import skipUnless
from unittest.mock import patch

import ddt
from django.conf import settings

from openedx.core.djangoapps.schedules import tasks
from openedx.core.djangoapps.schedules.tests.factories import ScheduleConfigFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory


@ddt.ddt
@skip_unless_lms
@skipUnless(
    'openedx.core.djangoapps.schedules' in settings.INSTALLED_APPS
    or 'openedx.core.djangoapps.schedules.apps.SchedulesConfig' in settings.INSTALLED_APPS,
    "Can't test schedules if the app isn't installed",
)
class TestMarketingPreferencesSuppression(CacheIsolationTestCase):
    """
    Exercises the ``is_marketing`` gate in ``_schedule_send``.
    """

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        self.site = SiteFactory.create()
        # ScheduleConfigFactory enables delivery for every schedule type by
        # default, so ``_is_delivery_enabled`` returns True and we actually
        # reach the marketing gate.
        ScheduleConfigFactory.create(site=self.site)
        self.user = UserFactory.create()

    def _set_marketing_preferences(self, preferences):
        """
        Persist ``marketing_preferences`` into the user's ``UserProfile.meta``
        JSON blob (the field read by ``_schedule_send``).
        """
        profile = UserProfile.objects.get(user=self.user)
        profile.set_meta({'marketing_preferences': preferences})
        profile.save()

    def _call_schedule_send(self, is_marketing):
        """
        Invoke ``_schedule_send`` for the recurring-nudge delivery config with
        ``tasks.ace`` and ``tasks.Message`` patched.  Returns the ``ace`` mock so
        callers can assert on ``ace.send``.
        """
        with patch.object(tasks, 'ace') as mock_ace, patch.object(tasks, 'Message') as mock_message:
            # Route the (mocked) message back to our real user so the profile
            # lookup inside ``_schedule_send`` finds the meta we set up.
            mock_message.from_string.return_value.recipient.lms_user_id = self.user.id
            tasks._schedule_send(
                'msg-str',
                self.site.id,
                'deliver_recurring_nudge',
                tasks.RECURRING_NUDGE_LOG_PREFIX,
                is_marketing=is_marketing,
            )
        return mock_ace

    def test_marketing_without_futures_preference_is_suppressed(self):
        """(a) is_marketing=True + meta WITHOUT 'Futures eLearning' -> not sent."""
        self._set_marketing_preferences(['Some Other Newsletter'])
        mock_ace = self._call_schedule_send(is_marketing=True)
        assert not mock_ace.send.called

    def test_marketing_with_no_preferences_is_suppressed(self):
        """is_marketing=True + empty marketing_preferences -> not sent."""
        self._set_marketing_preferences([])
        mock_ace = self._call_schedule_send(is_marketing=True)
        assert not mock_ace.send.called

    def test_marketing_with_futures_preference_is_sent(self):
        """(b) is_marketing=True + meta WITH 'Futures eLearning' -> sent."""
        self._set_marketing_preferences(['Futures eLearning'])
        mock_ace = self._call_schedule_send(is_marketing=True)
        assert mock_ace.send.called

    @ddt.data(['Futures eLearning'], ['Some Other Newsletter'], [])
    def test_non_marketing_always_sent(self, preferences):
        """(c) is_marketing=False -> always sent regardless of preferences."""
        self._set_marketing_preferences(preferences)
        mock_ace = self._call_schedule_send(is_marketing=False)
        assert mock_ace.send.called
