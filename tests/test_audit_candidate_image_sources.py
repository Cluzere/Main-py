import importlib.util
import os
import unittest

CANDIDATE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "audit-recovery", "review-round-2", "revisi_audit_final_main.py",
)
spec = importlib.util.spec_from_file_location("clouzer_image_source_candidate", CANDIDATE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ImageSourceMergeTests(unittest.TestCase):
    def test_merges_new_and_replied_images(self):
        new_images = ["data:image/png;base64,NEW"]
        replied_images = ["data:image/jpeg;base64,REPLIED"]
        self.assertEqual(
            module.merge_image_sources(new_images, replied_images),
            new_images + replied_images,
        )

    def test_deduplicates_sources_without_changing_priority(self):
        self.assertEqual(
            module.merge_image_sources(
                ["https://cdn.example/new.png", "https://cdn.example/shared.png"],
                ["https://cdn.example/shared.png", "https://cdn.example/reply.png"],
            ),
            [
                "https://cdn.example/new.png",
                "https://cdn.example/shared.png",
                "https://cdn.example/reply.png",
            ],
        )

    def test_respects_vision_cap(self):
        self.assertEqual(
            module.merge_image_sources(["data:image/png;base64,A"], ["data:image/png;base64,B"], limit=1),
            ["data:image/png;base64,A"],
        )

    def test_zero_cap_returns_no_sources(self):
        self.assertEqual(
            module.merge_image_sources(["data:image/png;base64,A"], limit=0),
            [],
        )

    def test_ignores_invalid_image_source_values(self):
        self.assertEqual(
            module.merge_image_sources(
                ["", None, "file:///tmp/image.png", "https://cdn.example/ok.png"],
                limit=5,
            ),
            ["https://cdn.example/ok.png"],
        )


if __name__ == "__main__":
    unittest.main()
