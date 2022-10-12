# (C) Datadog, Inc. 2018-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import json
from copy import deepcopy

import mock
import pytest
from mock import ANY, patch

from datadog_checks.teamcity import TeamCityCheck

from .common import (
    BUILD_STATS_METRICS,
    BUILD_TAGS,
    CHECK_NAME,
    LEGACY_BUILD_TAGS,
    LEGACY_INSTANCE,
    NEW_FAILED_BUILD,
    NEW_SUCCESSFUL_BUILD,
    TESTS_SERVICE_CHECK_RESULTS,
    USE_OPENMETRICS,
    get_fixture_path,
)


@pytest.mark.skipif(USE_OPENMETRICS == 'true', reason='Event collection is not available in OpenMetricsV2 instance')
@pytest.mark.integration
def test_build_event(aggregator, legacy_instance):
    legacy_instance['build_config_metrics'] = False
    legacy_instance['test_result_metrics'] = False
    legacy_instance['build_problem_checks'] = False

    teamcity = TeamCityCheck(CHECK_NAME, {}, [legacy_instance])

    responses = json.load(open(get_fixture_path('event_responses.json'), 'r'))

    json_responses = [
        responses['legacy_build_details'],
        responses['legacy_last_build'],
        responses['legacy_new_builds'],
    ]

    with mock.patch('datadog_checks.base.utils.http.requests') as req:
        mock_resp = mock.MagicMock(status_code=200)
        mock_resp.json.side_effect = json_responses
        req.get.return_value = mock_resp
        teamcity.check(legacy_instance)

    assert len(aggregator.metric_names) == 0
    aggregator.assert_event(
        event_type='build',
        host='buildhost42.dtdg.co',
        msg_text='Build Number: 1\nDeployed To: buildhost42.dtdg.co\n\nMore Info: '
        'http://localhost:8111/viewLog.html?buildId=1&buildTypeId=SampleProject_Build',
        msg_title='Build for Legacy test build successful',
        source_type_name='teamcity',
        alert_type='success',
        tags=LEGACY_BUILD_TAGS + ['build_id:1', 'build_number:1', 'build'],
        count=1,
    )

    aggregator.reset()

    # One more check should not create any more events
    with mock.patch('datadog_checks.base.utils.http.requests') as req:
        mock_resp = mock.MagicMock(status_code=200)
        mock_resp.json.side_effect = [responses['legacy_no_new_builds']]
        req.get.return_value = mock_resp
        teamcity.check(legacy_instance)

    aggregator.assert_event(msg_title="", msg_text="", count=0)


@pytest.mark.parametrize(
    'extra_config, expected_http_kwargs',
    [
        pytest.param({'ssl_validation': True}, {'verify': True}, id="legacy ssl config True"),
        pytest.param({'ssl_validation': False}, {'verify': False}, id="legacy ssl config False"),
        pytest.param({}, {'verify': True}, id="legacy ssl config unset"),
    ],
)
def test_config(extra_config, expected_http_kwargs):
    instance = deepcopy(LEGACY_INSTANCE)
    instance.update(extra_config)
    check = TeamCityCheck(CHECK_NAME, {}, [instance])

    with patch('datadog_checks.base.utils.http.requests.get') as r:
        check.check(instance)

        http_wargs = dict(
            auth=ANY,
            cert=ANY,
            headers=ANY,
            proxies=ANY,
            timeout=ANY,
            verify=ANY,
            allow_redirects=ANY,
        )
        http_wargs.update(expected_http_kwargs)

        r.assert_called_with(ANY, **http_wargs)


@pytest.mark.skipif(USE_OPENMETRICS == 'true', reason='These configs are not present in OpenMetrics')
@pytest.mark.parametrize(
    'build_config, expected_error',
    [
        pytest.param(
            {'projects': {'project_id': {}}}, 'Failed to establish a new connection', id="One `projects` config"
        ),
        pytest.param(
            {'build_configuration': 'build_config_id'},
            'Failed to establish a new connection',
            id="One `build_configurations` config",
        ),
        pytest.param({}, '`projects` must be configured', id="Missing projects config"),
        pytest.param(
            {'projects': {'project_id': {}}, 'build_configuration': 'build_config_id'},
            'Only one of `projects` or `build_configuration` must be configured, not both.',
            id="Redundant configs",
        ),
    ],
)
def test_validate_config(dd_run_check, build_config, expected_error, caplog):
    """
    Test that the `projects` and `build_configuration` config options are properly configured prior to running check.
    Note: The properly configured test cases would be expected to have a `Failed to establish a new connection`
    exception.
    """
    caplog.clear()
    config = {'server': 'server.name', 'use_openmetrics': False}

    instance = deepcopy(config)
    instance.update(build_config)

    check = TeamCityCheck(CHECK_NAME, {}, [instance])

    with pytest.raises(Exception) as e:
        dd_run_check(check)

    assert expected_error in str(e.value)


def test_collect_build_stats(aggregator, mock_http_response, instance, check):
    c = check(instance)
    c.build_tags = BUILD_TAGS

    mock_http_response(file_path=get_fixture_path("build_stats.json"))
    c._collect_build_stats(NEW_SUCCESSFUL_BUILD)

    for metric in BUILD_STATS_METRICS:
        metric_name = metric['name']
        expected_val = metric['value']
        expected_stats_tags = metric['tags']
        aggregator.assert_metric(metric_name, tags=expected_stats_tags, value=expected_val)

    aggregator.assert_all_metrics_covered()


def test_collect_test_results(aggregator, mock_http_response, instance, check):
    c = check(instance)
    c.build_tags = BUILD_TAGS

    mock_http_response(file_path=get_fixture_path("test_occurrences.json"))
    c._collect_test_results(NEW_SUCCESSFUL_BUILD)

    for res in TESTS_SERVICE_CHECK_RESULTS:
        expected_status = res['value']
        expected_tests_tags = res['tags']
        aggregator.assert_service_check('teamcity.test.results', status=expected_status, tags=expected_tests_tags)


def test_collect_build_problems(aggregator, mock_http_response, instance, check):
    mock_http_response(file_path=get_fixture_path('build_problems.json'))
    c = check(instance)
    c.build_tags = BUILD_TAGS
    expected_tags = BUILD_TAGS + ['problem_identity:python_build_error_identity', 'problem_type:TC_EXIT_CODE']

    c._collect_build_problems(NEW_FAILED_BUILD)

    aggregator.assert_service_check(
        'teamcity.build.problems',
        count=1,
        tags=expected_tags,
        status=c.CRITICAL,
    )


def test_handle_empty_builds(aggregator, mock_http_response, instance, legacy_instance, check):
    instances = [instance, legacy_instance]

    for inst in instances:
        c = check(inst)
        mock_http_response(file_path=get_fixture_path("init_no_builds.json"))
        c._initialize(project_id='SampleProject')
        aggregator.assert_service_check('teamcity.build.status', count=0)

        aggregator.reset()
