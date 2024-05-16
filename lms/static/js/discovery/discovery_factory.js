(function(define) {
    'use strict';

    define(['backbone', 'js/discovery/models/search_state', 'js/discovery/collections/filters',
        'js/discovery/views/search_form', 'js/discovery/views/courses_listing',
        'js/discovery/views/filter_bar', 'js/discovery/views/refine_sidebar'],
    function(Backbone, SearchState, Filters, SearchForm, CoursesListing, FilterBar, RefineSidebar) {
        return function(meanings, searchQuery, userLanguage, userTimezone) {
            var dispatcher = _.extend({}, Backbone.Events);
            var search = new SearchState();
            var filters = new Filters();
            var form = new SearchForm();
            var filterBar = new FilterBar({collection: filters});
            var refineSidebar = new RefineSidebar({
                collection: search.discovery.facetOptions,
                meanings: meanings
            });
            var listing;
            var courseListingModel = search.discovery;
            courseListingModel.userPreferences = {
                userLanguage: userLanguage,
                userTimezone: userTimezone
            };
            listing = new CoursesListing({model: courseListingModel});

            dispatcher.listenTo(form, 'search', function(query) {
                filters.reset();
                form.showLoadingIndicator();
                search.performSearch(query, filters.getTerms());
            });

            dispatcher.listenTo(refineSidebar, 'selectOption', function(type, query, name) {
                handleQueryParams(type, query, name);
            });

            dispatcher.listenTo(filterBar, 'clearFilter', removeFilter);

            dispatcher.listenTo(filterBar, 'clearAll', function() {
                form.doSearch('');
            });

            dispatcher.listenTo(listing, 'next', function() {
                search.loadNextPage();
            });

            dispatcher.listenTo(search, 'next', function() {
                listing.renderNext();
            });

            dispatcher.listenTo(search, 'search', function(query, total) {
                if (total > 0) {
                    form.showFoundMessage(total);
                    if (query) {
                        filters.add(
                            {type: 'search_query', query: query, name: quote(query)},
                            {merge: true}
                        );
                    }
                } else {
                    form.showNotFoundMessage(query);
                    filters.reset();
                }
                form.hideLoadingIndicator();
                listing.render();
                refineSidebar.render();
            });

            dispatcher.listenTo(search, 'error', function() {
                form.showErrorMessage(search.errorMessage);
                form.hideLoadingIndicator();
            });

            // kick off search on page refresh
            form.doSearch(searchQuery);

            // Utility functions
            function removeFilter(type) {
                form.showLoadingIndicator();
                filters.remove(type);
                if (type === 'search_query') {
                    form.doSearch('');
                } else {
                    search.refineSearch(filters.getTerms());
                }
            }

            function quote(string) {
                return '"' + string + '"';
            }

            // URL Query Parameter Functions
            function getQueryParams() {
                var params = {};
                var queryString = window.location.search.substring(1);
                var queryArray = queryString.split('&');
                for (var i = 0; i < queryArray.length; i++) {
                    var pair = queryArray[i].split('=');
                    params[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1]);
                }
                return params;
            }

            function handleQueryParams(type, query, name) {
                form.showLoadingIndicator();
                if (filters.get(type)) {
                    removeFilter(type);
                } else {
                    filters.add({type: type, query: query, name: name});
                    search.refineSearch(filters.getTerms());
                }
            }

            function onPageLoad() {
                var params = getQueryParams();
                if (params.type && params.query && params.name) {
                    handleQueryParams(params.type, params.query, params.name);
                }
            }

            // Run the function on page load
            $(function() {
                onPageLoad();
            });
        };
    });
}(define || RequireJS.define));
