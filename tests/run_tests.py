from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.compliance_api_tests import TESTS as COMPLIANCE_API_TESTS
from tests.escalation_contract_tests import TESTS as ESCALATION_CONTRACT_TESTS
from tests.finalization_contract_tests import TESTS as FINALIZATION_CONTRACT_TESTS
from tests.schema_runtime_tests import TESTS as SCHEMA_RUNTIME_TESTS
from tests.session_lifecycle_tests import TESTS as SESSION_LIFECYCLE_TESTS
from tests.test_support import ensure_data


def main() -> None:
    ensure_data()
    for test in [*SCHEMA_RUNTIME_TESTS, *SESSION_LIFECYCLE_TESTS, *FINALIZATION_CONTRACT_TESTS, *ESCALATION_CONTRACT_TESTS, *COMPLIANCE_API_TESTS]:
        test()
    print("PASS runtime/unit tests")


if __name__ == "__main__":
    main()
