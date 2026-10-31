"""Compare published base-image labels without downloading image layers."""

import json
import os
import re
import subprocess


BASE_DIGEST_LABEL = "org.opencontainers.image.base.digest"
REVISION_LABEL = "org.opencontainers.image.revision"
REPOSITORIES = ("docker.io/hnhs/ruby-dev", "ghcr.io/hicknhack-software/ruby-dev")


def inspect(reference, field):
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference,
         "--format", "{{json ." + field + "}}"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def platform_digest(reference):
    manifest = inspect(reference, "Manifest")
    if "manifests" in manifest:
        # Ignore other architectures and unknown/unknown attestation manifests.
        matches = [
            entry["digest"] for entry in manifest["manifests"]
            if entry.get("platform", {}).get("os") == "linux"
            and entry.get("platform", {}).get("architecture") == "amd64"
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one linux/amd64 image for {reference}")
        digest = matches[0]
    else:
        digest = manifest["digest"]
        config = inspect(f"{reference}@{digest}", "Image")
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError(f"Expected a linux/amd64 image for {reference}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"Invalid image digest for {reference}: {digest}")
    return digest


def check_base_image(base_tag, revision, force=False):
    # Resolve the upstream once and pin the build to exactly this platform digest.
    # Upstream lookup failures must fail the job, never be treated as unchanged.
    base_digest = platform_digest(f"docker.io/library/ruby:{base_tag}")
    if force:
        return base_digest, True, "Push or manual run: rebuilding explicitly."

    for repository in REPOSITORIES:
        reference = f"{repository}:{base_tag}"
        try:
            digest = platform_digest(reference)
            image = inspect(f"{reference}@{digest}", "Image")
            labels = image.get("config", {}).get("Labels") or {}
        except (subprocess.CalledProcessError, ValueError, KeyError) as error:
            # Missing/legacy images and registry errors cannot justify skipping.
            return base_digest, True, f"Cannot verify {reference}; rebuilding ({error})."
        if labels.get(BASE_DIGEST_LABEL) != base_digest:
            return base_digest, True, f"{reference} has a different or unrecorded base image."
        if labels.get(REVISION_LABEL) != revision:
            return base_digest, True, f"{reference} was built from a different source revision."

    return base_digest, False, "Both registries already have this base image and source revision."


def main():
    base_tag = os.environ["BASE_IMAGE_TAG"]
    digest, changed, reason = check_base_image(
        base_tag, os.environ["SOURCE_REVISION"],
        force=os.environ.get("FORCE_BUILD") == "true",
    )
    print(f"ruby:{base_tag}: {reason}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"base_digest={digest}\nchanged={str(changed).lower()}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"ruby:{base_tag}: {reason}\n\nBase digest: `{digest}`\n")


if __name__ == "__main__":
    main()
