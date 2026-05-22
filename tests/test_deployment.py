from src.orchestrator.deployment import (
    COMMIT_ANNOTATION,
    IMAGE_DIGEST_ANNOTATION,
    PACKAGE_VERSION_ANNOTATION,
    RELEASE_ID_ANNOTATION,
    SOURCE_REF_ANNOTATION,
    DeploymentHistory,
    DeploymentManifestRenderer,
    ReleaseIdentity,
)


def test_rendered_manifest_uses_immutable_release_annotations():
    renderer = DeploymentManifestRenderer()
    manifest = {
        "kind": "Deployment",
        "metadata": {
            "name": "agent-worker",
            "annotations": {
                RELEASE_ID_ANNOTATION: "main",
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "worker",
                            "image": "agent-worker:latest",
                        },
                    ],
                },
            },
        },
    }
    release = ReleaseIdentity(
        commit_sha="7f3f7a3a7d9a8c3ce1a26df53d4be47bfcc4f59d",
        package_version="2.4.1",
        image_digest=(
            "sha256:"
            "2f4c1bd9ee7c4b8607c2b2ebaa9c0f5c7d665c8fa78e3f6b4a"
            "9f7b03f5df7eab"
        ),
        source_ref="refs/heads/main",
    )

    rendered = renderer.render(manifest, release)
    annotations = rendered["workload"]["metadata"]["annotations"]

    assert annotations[RELEASE_ID_ANNOTATION] == release.release_id
    assert annotations[COMMIT_ANNOTATION] == release.commit_sha
    assert annotations[PACKAGE_VERSION_ANNOTATION] == release.package_version
    assert annotations[IMAGE_DIGEST_ANNOTATION] == release.image_digest
    assert annotations[SOURCE_REF_ANNOTATION] == "refs/heads/main"
    assert annotations[RELEASE_ID_ANNOTATION] not in {"main", "latest"}
    assert rendered["release"]["release_id"] == release.release_id


def test_deployment_history_can_query_by_release_id():
    history = DeploymentHistory()
    renderer = DeploymentManifestRenderer(history=history)
    release = ReleaseIdentity(
        commit_sha="38ea5a97f0bcb3cd45075d8b53236a581ed9b8f0",
        package_version="2.4.1",
        image_digest="sha256:4d2f1b917db324cbdceab34703bdc5f6",
        source_ref="refs/tags/staging",
    )

    renderer.render({"metadata": {"name": "api"}}, release)
    renderer.render({"metadata": {"name": "worker"}}, release)

    records = history.find_by_release_id(release.release_id)
    assert [record["workload"] for record in records] == ["api", "worker"]
    assert all(
        record["commit_sha"] == release.commit_sha
        for record in records
    )
    assert all(
        record["image_digest"] == release.image_digest
        for record in records
    )


def test_renderer_does_not_mutate_input_manifest():
    renderer = DeploymentManifestRenderer()
    manifest = {
        "metadata": {
            "name": "api",
            "annotations": {},
        },
    }
    release = ReleaseIdentity(
        commit_sha="5d8be0f8b171a719e1ea392c85ca95e8db7f5f62",
        package_version="2.4.1",
        image_digest="sha256:9f3c8bb2b7f44d7a9d5c936c7bc6c23e",
    )

    rendered = renderer.render(manifest, release)

    assert manifest["metadata"]["annotations"] == {}
    assert rendered["workload"]["metadata"]["annotations"][
        RELEASE_ID_ANNOTATION
    ] == release.release_id
