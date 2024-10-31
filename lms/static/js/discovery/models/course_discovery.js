(function(define) {
    define([
        'underscore',
        'backbone',
        'js/discovery/models/course_card',
        'js/discovery/models/facet_option'
    ], function(_, Backbone, CourseCard, FacetOption) {
        'use strict';

        return Backbone.Model.extend({
            url: '/search/course_discovery/',
            jqhxr: null,

            defaults: {
                totalCount: 0,
                latestCount: 0
            },

            initialize: function() {
                this.courseCards = new Backbone.Collection([], {model: CourseCard});
                this.facetOptions = new Backbone.Collection([], {model: FacetOption});
            },

            parse: function(response) {
                var courses = response.results || [];
                var facets = response.aggs || {};
                var options = this.facetOptions;

                // Determine if the current page is /videos
                var isVideosPage = window.location.pathname === '/videos';

                // Filter courses based on the current page
                courses = courses.filter(function(course) {
                    return isVideosPage ? course.data.course_type === 'video' : course.data.course_type !== 'video';
                });

                // Add courses to the collection
                this.courseCards.add(_.pluck(courses, 'data'));
                this.set({
                    totalCount: courses.length,
                    latestCount: courses.length
                });

                // Remove unwanted facets
                if (isVideosPage) {
                    if (facets.course_type) {
                        delete facets.course_type;
                    }
                } else {
                    if (facets.course_type && facets.course_type.terms && facets.course_type.terms.video) {
                        delete facets.course_type.terms.video;
                    }
                }

                // Go through results and add any missing facet values
                var facetTypes = Object.keys(facets);
                var allFacets = {};

                facetTypes.forEach(function(facetType) {
                    allFacets[facetType] = {};
                });

                courses.forEach(function(course) {
                    facetTypes.forEach(function(facetType) {
                        var facetValue = course.data[facetType];
                        if (facetValue) {
                            if (Array.isArray(facetValue)) {
                                facetValue.forEach(function(value) {
                                    allFacets[facetType][value] = (allFacets[facetType][value] || 0) + 1;
                                });
                            } else {
                                allFacets[facetType][facetValue] = (allFacets[facetType][facetValue] || 0) + 1;
                            }
                        }
                    });
                });

                // Filter facets to include only those relevant to the displayed list
                _.each(allFacets, function(values, facetType) {
                    if (!facets[facetType]) {
                        facets[facetType] = {
                            terms: values,
                            total: _.reduce(_.values(values), function(memo, num) { return memo + num; }, 0),
                            other: 0
                        };
                    } else {
                        _.each(values, function(count, term) {
                            if (!facets[facetType].terms[term]) {
                                facets[facetType].terms[term] = count;
                            } else {
                                facets[facetType].terms[term] += count;
                            }
                        });
                    }
                });
                // Add facet options to the collection
                _(facets).each(function(obj, key) {
                    var sortedTerms = _.keys(obj.terms).sort();
                    sortedTerms.forEach(function(term) {
                        options.add({
                            facet: key,
                            term: term,
                            count: obj.terms[term]
                        }, {merge: true});
                    });
                });
            },

            reset: function() {
                this.set({
                    totalCount: 0,
                    latestCount: 0
                });
                this.courseCards.reset();
                this.facetOptions.reset();
            },

            latest: function() {
                return this.courseCards.last(this.get('latestCount'));
            }

        });
    });
}(define || RequireJS.define));
