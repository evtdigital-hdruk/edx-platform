"""
Management command to sync platform users with hubspot
./manage.py lms sync_hubspot_contacts
./manage.py lms sync_hubspot_contacts --initial-sync-days=7 --batch-size=20
"""


import json
import time
import traceback
import urllib.parse  # pylint: disable=import-error
from datetime import datetime, timedelta, timezone
from textwrap import dedent

import requests
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from common.djangoapps.student.models import UserProfile
from django.core.management.base import BaseCommand, CommandError
from requests.exceptions import HTTPError

from common.djangoapps.student.models import UserAttribute
from common.djangoapps.util.query import use_read_replica_if_available
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration

HUBSPOT_API_BASE_URL = 'https://api.hubapi.com'
futures_marketing_options_list = ["Futures eLearning", "Training Bulletin"]


class Command(BaseCommand):
    """
    Command to create contacts in hubspot for those partner who has enabled hubspot integration.
    This command is suppose to sync contact with hubspot on daily basis.
    """
    help = dedent(__doc__).strip()

    def _get_hubspot_enabled_sites(self):
        """
        Returns: list of site configurations having hubspot integration enabled
        """
        site_confs = SiteConfiguration.objects.all()
        hubspot_sites = [
            site_conf for site_conf in site_confs
            if site_conf.get_value('HUBSPOT_API_KEY')
        ]
        return hubspot_sites

    def _get_users_queryset(self, initial_days):
        """
        initial_days: numbers of days to go back from today
        :return: users queryset
        """
        start_date = datetime.now().date() - timedelta(initial_days)
        end_date = datetime.now().date()
        self.stdout.write(f'Getting users from {start_date} to {end_date}')
        users_qs = User.objects.filter(
            date_joined__date__gte=start_date,
            date_joined__date__lte=end_date
        ).order_by('id')
        return use_read_replica_if_available(users_qs)

    def _get_batched_users(self, site_domain, users_queryset, offset, users_query_batch_size):
        """
        Args:
            site_domain: site where we need unsynced users
            users_queryset: users_queryset to slice
            users_query_batch_size: slice size

        Returns: site users

        """

        self.stdout.write(
            'Fetching Users for site {site} from {start} to {end}'.format(
                site=site_domain, start=offset, end=offset + users_query_batch_size
            )
        )
        users = users_queryset.select_related('profile')[offset: offset + users_query_batch_size]
        site_users = [
            user for user in users
            if UserAttribute.get_user_attribute(user, 'created_on_site') == site_domain
        ]
        self.stdout.write(f'\tSite Users={len(site_users)}')

        return site_users

    def _split_full_name(self, full_name):
        name_parts = full_name.split(" ", 1)
        if len(name_parts) > 1:
            first_name = name_parts[0]
            last_name = name_parts[1]
        else:
            first_name = ''
            last_name = ''
        return first_name, last_name

    def _add_custom_hubspot_properties(self, site_conf):
        custom_property_job_title = {
            "hidden": False,
            "displayOrder": -1,
            "description": "Contact's Job Title on Futures",
            "label": "Futures Job Title",
            "type": "string",
            "groupName": "hdruk_properties",
            "name": "futuresjobtitle",
            "fieldType": "textarea",
            "formField": True,
            "hasUniqueValue": False,
            "externalOptions": False
        }
        custom_property_industry  = {
            "hidden": False,
            "displayOrder": -1,
            "description": "Contact's profession on Futures",
            "label": "Futures Industry",
            "type": "string",
            "groupName": "hdruk_properties",
            "name": "futuresindustry",
            "fieldType": "textarea",
            "formField": True,
            "hasUniqueValue": False,
            "externalOptions": False
        }
        custom_property_goals = {
            "hidden": False,
            "displayOrder": -1,
            "description": "Goals that learner would like to achieve with Futures",
            "label": "Futures Goals",
            "type": "string",
            "groupName": "hdruk_properties",
            "name": "futuresgoals",
            "fieldType": "textarea",
            "formField": True,
            "hasUniqueValue": False,
            "externalOptions": False
        }
        custom_property_bio = {
            "hidden": False,
            "displayOrder": -1,
            "description": "Contact's biography on Futures",
            "label": "Futures Biography",
            "type": "string",
            "groupName": "hdruk_properties",
            "name": "futuresbio",
            "fieldType": "textarea",
            "formField": True,
            "hasUniqueValue": False,
            "externalOptions": False
        }
        custom_property_last_synced_from_futures = {
            "hidden": False,
            "displayOrder": -1,
            "description": "Date and time that contact was last synced with Futures data.",
            "label": "Last Synced with Futures",
            "type": "datetime",
            "groupName": "hdruk_properties",
            "name": "last_synced_with_futures",
            "fieldType": "date",
            "formField": False,
            "hasUniqueValue": False,
            "externalOptions": False
        }
        custom_properties = [custom_property_job_title, custom_property_industry, custom_property_goals, custom_property_bio, custom_property_last_synced_from_futures]
        base_url = f"{HUBSPOT_API_BASE_URL}/"
        api_key = site_conf.get_value('HUBSPOT_API_KEY')
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        for custom_property in custom_properties:
            api_url = urllib.parse.urljoin(base_url, f'crm/v3/properties/contact/{custom_property["name"]}')
            try:
                response = requests.get(api_url, headers=headers)
                if response.status_code == 200:
                    self.stdout.write(f'Updating {custom_property["label"]} property as it already exists.')
                    response = requests.patch(api_url, data=json.dumps(custom_property), headers=headers)
                elif response.status_code == 404:
                    api_url = urllib.parse.urljoin(base_url, 'crm/v3/properties/contact')
                    self.stdout.write(f'Creating {custom_property["label"]} property.')
                    response = requests.post(api_url, data=json.dumps(custom_property), headers=headers)
                else:
                    response.raise_for_status()
                response.raise_for_status()
            except HTTPError as err:
                self.stderr.write(str(err))
                return 0
        return 1

    def _update_marketing_preferences(self, contact, crm_preference, override_platform_preferences=False):
        contact_marketing_preferences_property = next((prop for prop in contact["properties"] if prop["property"] == "communication_preference"), None)
        crm_preference_list = set(crm_preference.split(';'))
        current_futures_preference_list = set(contact_marketing_preferences_property["value"].split(';'))
        other_communication_preferences = crm_preference_list.difference(futures_marketing_options_list)
        updated_crm_preferences = crm_preference_list if override_platform_preferences else current_futures_preference_list.union(other_communication_preferences)

        if override_platform_preferences:
            user = User.objects.get(email=contact['email'])
            user_profile = UserProfile.objects.get(user=user)
            meta = user_profile.get_meta()
            meta['marketing_preferences'] = list(crm_preference_list.intersection(futures_marketing_options_list))
            user_profile.set_meta(meta)
            user_profile.save()
            
        contact_marketing_preferences_property["value"] = ";".join(filter(None,updated_crm_preferences))

    def _sync_with_hubspot(self, users_batch, site_conf):
        """
        Sync batch of users with hubspot
        """
        contacts = []
        email_list = []
        for user in users_batch:
            if not hasattr(user, 'profile'):
                self.stdout.write(f'skipping user {user} due to no profile found')
                continue
            if not user.profile.meta:
                self.stdout.write(f'skipping user {user} due to no profile meta found')
                continue
            try:
                meta = json.loads(user.profile.meta)
            except ValueError:
                self.stdout.write(f'skipping user {user} due to invalid profile meta found')
                continue
            full_name = user.profile.name if user.profile.name else ""
            first_name, last_name = self._split_full_name(full_name)
            contact = {
                "email": user.email,
                "properties": [
                    {
                        "property": "firstname",
                        "value": meta.get('first_name', first_name)
                    },
                    {
                        "property": "lastname",
                        "value": meta.get('last_name', last_name)
                    },
                    {
                        "property": "company",
                        "value": meta.get('company', '')
                    },
                    {
                        "property": "futuresjobtitle",
                        "value": meta.get('job_title', '')
                    },
                    {
                        "property": "futuresindustry",
                        "value": meta.get('profession', '')
                    },
                    {
                        "property": "state",
                        "value": user.profile.get_state_display()
                    },
                    {
                        "property": "country",
                        "value": user.profile.get_country_display()
                    },
                    {
                        "property": "gender",
                        "value": user.profile.get_gender_display()
                    },
                    {
                        "property": "degree",
                        "value": user.profile.get_level_of_education_display()
                    },
                    {
                        "property": "futuresgoals",
                        "value": user.profile.goals
                    },
                    {
                        "property": "futuresbio",
                        "value": user.profile.bio
                    },
                    {
                        "property": "communication_preference",
                        "value": ";".join(meta.get('marketing_preferences', ''))
                    },
                    {
                        "property": "last_synced_with_futures",
                        "value": int(datetime.now(timezone.utc).timestamp() * 1000)
                    },
                ]
            }
            contacts.append(contact)
            email_list.append({
                "id": user.email
            })

        api_key = site_conf.get_value('HUBSPOT_API_KEY')
        api_url = urllib.parse.urljoin(f"{HUBSPOT_API_BASE_URL}/", 'crm/v3/objects/contacts/batch/read')
        retrieve_contacts_by_id = {
            "propertiesWithHistory": [
                "communication_preference",
            ],
            "properties": [
                "email",
                "communication_preference",
                "last_synced_with_futures"
            ],
            "idProperty": "email",
            "inputs": email_list,
        }
        try:
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            }
            response = requests.post(api_url, json=retrieve_contacts_by_id, headers=headers)
            crm_preferences = {
                result['properties']['email']: result['properties']['communication_preference']
                for result in (res for res in response.json()['results'] if res['properties']['communication_preference'] != None)
            }
            should_override_platform_preferences = [
                result['properties']['email']
                for result in (
                    res for res in response.json()['results']
                    if (
                        len(res['propertiesWithHistory']['communication_preference']) >0) and
                        datetime.strptime((res['propertiesWithHistory']['communication_preference'][0]['timestamp']),"%Y-%m-%dT%H:%M:%S.%fZ") > 
                        datetime.strptime((res['properties']['last_synced_with_futures']),"%Y-%m-%dT%H:%M:%S.%fZ") and
                        res['propertiesWithHistory']['communication_preference'][0]['sourceId'] != site_conf.get_value('HUBSPOT_APP_ID')
                )
            ]
            for contact in contacts:
                if crm_preferences.get(contact["email"]) != None:
                    self._update_marketing_preferences(contact, crm_preferences.get(contact["email"]),True if contact['email'] in should_override_platform_preferences else False)
            response.raise_for_status()
        except HTTPError as ex:
            message = 'An error occurred while retrieving contacts for site {domain}, {message}'.format(
                domain=site_conf.site.domain, message=str(ex)
            )
            self.stderr.write(message)
            return 0
        api_url = urllib.parse.urljoin(f"{HUBSPOT_API_BASE_URL}/", 'contacts/v1/contact/batch/')
        try:
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            }
            response = requests.post(api_url, json=contacts, headers=headers)
            response.raise_for_status()
            return len(contacts)
        except HTTPError as ex:
            message = 'An error occurred while syncing batch of contacts for site {domain}, {message}'.format(
                domain=site_conf.site.domain, message=str(ex)
            )
            self.stderr.write(message)
            return 0

    def _sync_site(self, site_conf, users_queryset, users_count, contacts_batch_size):
        """
            Syncs a single site
        """
        site_domain = site_conf.site.domain
        self.stdout.write(f'Syncing process started for site {site_domain}')

        offset = 0
        users_queue = []
        users_query_batch_size = 5000
        successfully_synced_contacts = 0

        while offset < users_count:
            is_last_iteration = (offset + users_query_batch_size) >= users_count
            self.stdout.write(
                'Syncing users batch from {start} to {end} for site {site}'.format(
                    start=offset, end=offset + users_query_batch_size, site=site_domain
                )
            )
            users_queue += self._get_batched_users(site_domain, users_queryset, offset, users_query_batch_size)
            while len(users_queue) >= contacts_batch_size \
                    or (is_last_iteration and users_queue):  # for last iteration need to empty users_queue
                users_batch = users_queue[:contacts_batch_size]
                del users_queue[:contacts_batch_size]
                successfully_synced_contacts += self._sync_with_hubspot(users_batch, site_conf)
                time.sleep(0.1)  # to make sure request per second could not exceed by 10
            self.stdout.write(
                'Successfully synced users batch from {start} to {end} for site {site}'.format(
                    start=offset, end=offset + users_query_batch_size, site=site_domain
                )
            )
            offset += users_query_batch_size

        self.stdout.write(
            '{count} contacts found and synced for site {site}'.format(
                count=successfully_synced_contacts, site=site_domain
            )
        )

    def add_arguments(self, parser):
        """
        Definition of arguments this command accepts
        """
        parser.add_argument(
            '--initial-sync-days',
            default = 1,
            dest = 'initial_sync_days',
            type = int,
            help = 'Number of days before today to start sync',
        )
        parser.add_argument(
            '--batch-size',
            default = 100,
            dest = 'batch_size',
            type = int,
            help = 'Size of contacts batch to be sent to hubspot',
        )

    def handle(self, *args, **options):
        """
        Main command handler
        """
        initial_sync_days = options['initial_sync_days']
        batch_size = options['batch_size']
        try:
            self.stdout.write(f'Command execution started with options = {options}.')
            hubspot_sites = self._get_hubspot_enabled_sites()
            self.stdout.write(f'{len(hubspot_sites)} hubspot enabled sites found.')
            users_queryset = self._get_users_queryset(initial_sync_days)
            users_count = users_queryset.count()
            self.stdout.write(f'Users count={users_count}')
            for site_conf in hubspot_sites:
                self._add_custom_hubspot_properties(site_conf)
                self._sync_site(site_conf, users_queryset, users_count, batch_size)

        except Exception as ex:
            traceback.print_exc()
            raise CommandError('Command failed with traceback %s' % str(ex))  # lint-amnesty, pylint: disable=raise-missing-from
