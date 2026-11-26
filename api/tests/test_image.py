"""The production image, built like Heroku builds it (api/ only as the context), running the samples.

Slow (builds the image) and needs Docker and git, so it only runs when asked:

    TALLIED_IMAGE_TEST=1 uv run pytest tests/test_image.py -s

The same check runs without the project installed: python api/scripts/image_test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "image_test.py"


@pytest.mark.skipif(os.environ.get("TALLIED_IMAGE_TEST") != "1" or not shutil.which("docker"),
                    reason="set TALLIED_IMAGE_TEST=1 on a machine with Docker")
def test_production_image_runs_every_sample_end_to_end() -> None:
    ca = os.environ.get("TALLIED_IMAGE_TEST_CA")
    extra = ["--extra-ca", ca] if ca else []
    res = subprocess.run([sys.executable, str(SCRIPT), *extra], capture_output=True, text=True, timeout=1800)
    print(res.stdout[-4000:])
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
