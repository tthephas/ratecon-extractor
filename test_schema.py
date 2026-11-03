import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schema import RateConfirmation

KEYS = Path(__file__).parent / "answer_keys"


@pytest.mark.parametrize("name", ["rc1_peachtree", "rc2_lakeshore", "rc3_redclay", "rc5_bluegrass"])
def test_good_answer_keys_pass(name):
    data = json.loads((KEYS / f"{name}.json").read_text())
    RateConfirmation.model_validate(data)


def test_rc4_total_mismatch_is_caught():
    data = json.loads((KEYS / "rc4_summit.json").read_text())
    with pytest.raises(ValidationError, match="add up"):
        RateConfirmation.model_validate(data)
