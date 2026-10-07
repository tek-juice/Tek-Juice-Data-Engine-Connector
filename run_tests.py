"""Convenience script to run all tests from the project root."""
import subprocess, sys, os
os.chdir(os.path.dirname(__file__))
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "--tb=short", "-v"],
    capture_output=False,
)
sys.exit(result.returncode)
