import pytest

from datadog_checks.dev.testing import requires_py3
from datadog_checks.ibm_i import IbmICheck


@pytest.mark.e2e
@requires_py3
def test_e2e(dd_agent_check, aggregator, instance):
    aggregator = dd_agent_check(instance)
    aggregator.assert_service_check('ibm_i', IbmCheck.CRITICAL)
