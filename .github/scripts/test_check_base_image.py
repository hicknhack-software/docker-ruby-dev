import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "check_base_image", Path(__file__).with_name("check-base-image.py")
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

BASE = "sha256:" + "a" * 64
OLD_BASE = "sha256:" + "b" * 64
DEV = "sha256:" + "c" * 64
REVISION = "source-revision"


def index(digest):
    return {"manifests": [
        {"digest": digest, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": OLD_BASE, "platform": {"os": "linux", "architecture": "arm64"}},
        {"digest": OLD_BASE, "platform": {"os": "unknown", "architecture": "unknown"}},
    ]}


class BaseImageTests(unittest.TestCase):
    def setUp(self):
        self.images = {
            repository: {"config": {"Labels": {
                checker.BASE_DIGEST_LABEL: BASE,
                checker.REVISION_LABEL: REVISION,
            }}} for repository in checker.REPOSITORIES
        }
        self.calls = []
        self.mock = patch.object(checker, "inspect", side_effect=self.inspect).start()
        self.addCleanup(patch.stopall)

    def inspect(self, reference, field):
        self.calls.append((reference, field))
        if reference.startswith("docker.io/library/ruby:"):
            return index(BASE)
        repository = reference.split(":")[0]
        if field == "Manifest":
            return index(DEV)
        self.assertTrue(reference.endswith("@" + DEV))
        return self.images[repository]

    def check(self, force=False):
        return checker.check_base_image("3.4.12", REVISION, force)

    def test_unchanged_images_in_both_registries_skip(self):
        self.assertEqual(self.check()[:2], (BASE, False))
        self.assertEqual(len(self.calls), 5)

    def test_changed_base_in_either_registry_rebuilds(self):
        for repository in checker.REPOSITORIES:
            with self.subTest(repository=repository):
                self.images[repository]["config"]["Labels"][checker.BASE_DIGEST_LABEL] = OLD_BASE
                self.assertTrue(self.check()[1])
                self.images[repository]["config"]["Labels"][checker.BASE_DIGEST_LABEL] = BASE

    def test_source_change_rebuilds_even_with_same_base(self):
        self.images[checker.REPOSITORIES[1]]["config"]["Labels"][checker.REVISION_LABEL] = "old"
        self.assertTrue(self.check()[1])

    def test_legacy_images_without_labels_rebuild(self):
        for labels in (None, {}):
            self.images[checker.REPOSITORIES[0]]["config"]["Labels"] = labels
            self.assertTrue(self.check()[1])

    def test_missing_image_or_registry_failure_rebuilds(self):
        self.mock.side_effect = [index(BASE), subprocess.CalledProcessError(1, "docker")]
        self.assertTrue(self.check()[1])

    def test_upstream_failure_is_fatal(self):
        self.mock.side_effect = subprocess.CalledProcessError(1, "docker")
        with self.assertRaises(subprocess.CalledProcessError):
            self.check()

    def test_push_and_manual_runs_force_build(self):
        self.assertEqual(self.check(force=True)[:2], (BASE, True))
        self.assertEqual(len(self.calls), 1)

    def test_other_architecture_and_attestation_changes_are_ignored(self):
        before = index(BASE)
        after = copy.deepcopy(before)
        after["manifests"][1]["digest"] = DEV
        after["manifests"][2]["digest"] = DEV
        after["digest"] = OLD_BASE
        self.mock.side_effect = [before, after]
        self.assertEqual(checker.platform_digest("ruby:before"), BASE)
        self.assertEqual(checker.platform_digest("ruby:after"), BASE)

    def test_missing_or_ambiguous_target_platform_fails(self):
        for manifests in ([], index(BASE)["manifests"][:1] * 2):
            self.mock.return_value = {"manifests": manifests}
            self.mock.side_effect = None
            with self.assertRaises(ValueError):
                checker.platform_digest("ruby:invalid")

    def test_invalid_digest_fails(self):
        self.mock.side_effect = [index("not-a-digest")]
        with self.assertRaises(ValueError):
            self.check()

    def test_single_platform_manifest_is_supported(self):
        self.mock.side_effect = [
            {"digest": BASE}, {"os": "linux", "architecture": "amd64"},
        ]
        self.assertEqual(checker.platform_digest("ruby:single"), BASE)

    def test_single_platform_image_with_wrong_architecture_fails(self):
        self.mock.side_effect = [
            {"digest": BASE}, {"os": "linux", "architecture": "arm64"},
        ]
        with self.assertRaises(ValueError):
            checker.platform_digest("ruby:arm64")

    def test_github_outputs_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            summary = Path(temporary) / "summary"
            with patch.dict(checker.os.environ, {
                "BASE_IMAGE_TAG": "3.4.12", "SOURCE_REVISION": REVISION,
                "FORCE_BUILD": "false", "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            }):
                checker.main()
            self.assertIn(f"base_digest={BASE}\nchanged=false\n", output.read_text())
            self.assertIn("Both registries", summary.read_text())


if __name__ == "__main__":
    unittest.main()
