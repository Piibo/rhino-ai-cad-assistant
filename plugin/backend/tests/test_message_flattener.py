"""Regression net for the message flattener (model-facing block conversion).

These are the plugin backend's first unit tests. They lock the behaviour of
``agent/message_flattener.py`` — the layer that converts plugin-specific
content blocks (sketch / selection / point_pick / component_pick) into the
native text+image blocks the Anthropic API receives. That layer decides
*what the model actually sees*, so a silent change here would confound the
user study; this suite is the safety net against that.

Why the importlib dance: importing ``backend.agent.message_flattener`` via the
package pulls the full agent package __init__, which imports Rhino-only deps
(``shared`` etc.) that don't exist outside Rhino. The flattener module itself
only needs ``logging`` + ``typing``, so we load the file directly and test the
pure functions in isolation — no Rhino, no new dependency (stdlib unittest).

Run (from rhaino/plugin):
    python backend/tests/test_message_flattener.py
or:
    python -m unittest -v backend.tests.test_message_flattener   # if backend importable
"""
from __future__ import annotations

import importlib.util
import os
import unittest

# --- load the module under test directly from its file (bypasses package) ---
_MOD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "agent", "message_flattener.py"
)
_spec = importlib.util.spec_from_file_location("message_flattener_under_test", _MOD_PATH)
mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mf)

# An ImageSource as the frontend/schema serialises it (dict with data + media_type).
IMG = {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
OTHER_IMG = {"type": "base64", "media_type": "image/jpeg", "data": "WllY"}  # distinct data


def kinds(out):
    return [b["type"] for b in out]


def images(out):
    return [b for b in out if b["type"] == "image"]


def texts(out):
    return [b["text"] for b in out if b["type"] == "text"]


class TestImageBlock(unittest.TestCase):
    def test_dict_with_data_builds_native_image(self):
        self.assertEqual(
            mf._image_block(IMG),
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
            },
        )

    def test_media_type_defaults_to_png_when_absent(self):
        self.assertEqual(
            mf._image_block({"data": "X"})["source"]["media_type"], "image/png"
        )

    def test_default_media_type_param_is_used_when_absent(self):
        self.assertEqual(
            mf._image_block({"data": "X"}, default_media_type="image/jpeg")["source"][
                "media_type"
            ],
            "image/jpeg",
        )

    def test_carried_media_type_wins_over_default(self):
        self.assertEqual(
            mf._image_block(IMG, default_media_type="image/png")["source"]["media_type"],
            "image/jpeg",
        )

    def test_none_and_non_dict_and_empty_return_none(self):
        self.assertIsNone(mf._image_block(None))
        self.assertIsNone(mf._image_block("not-a-dict"))
        self.assertIsNone(mf._image_block({"media_type": "image/png"}))  # no data


class TestFlattenSketch(unittest.TestCase):
    def test_annotated_single_view_emits_deixis_caption_and_image(self):
        out = mf._flatten_sketch(
            {
                "type": "sketch",
                "svg": "<svg><path d='M1 1 L2 2'/></svg>",
                "rendered_png": IMG,
                "background": IMG,
                "width": 1000,
                "height": 750,
            }
        )
        self.assertEqual(kinds(out), ["text", "image"])
        self.assertIn("Skizze des Users", texts(out)[0])
        self.assertEqual(images(out)[0]["source"]["data"], "QUJD")

    def test_no_strokes_snapshot_emits_snapshot_caption_and_image(self):
        # The 22.06.2026 bug fix: a strokeless snapshot stages the composite as
        # rendered_png so it actually reaches the model, with a snapshot caption.
        out = mf._flatten_sketch(
            {"type": "sketch", "svg": "", "rendered_png": IMG, "width": 1024, "height": 768}
        )
        self.assertEqual(kinds(out), ["text", "image"])
        self.assertIn("Multi-View-Snapshot", texts(out)[0])
        self.assertIn("KEINE Striche", texts(out)[0])

    def test_legacy_old_row_falls_back_to_background_image(self):
        out = mf._flatten_sketch(
            {
                "type": "sketch",
                "svg": "<svg><path d='M1 1'/></svg>",
                "background": IMG,  # no rendered_png (pre-rendered_png DB rows)
                "width": 1000,
                "height": 750,
            }
        )
        self.assertEqual(kinds(out), ["text", "image"])
        self.assertIn("Skizze des Users", texts(out)[0])
        self.assertEqual(images(out)[0]["source"]["data"], "QUJD")

    def test_whitespace_only_svg_is_treated_as_snapshot(self):
        out = mf._flatten_sketch(
            {"type": "sketch", "svg": "   ", "rendered_png": IMG, "width": 800, "height": 600}
        )
        self.assertIn("Multi-View-Snapshot", texts(out)[0])

    def test_no_image_at_all_yields_caption_only(self):
        # Degenerate shape (the pre-fix strokeless bug): no image fields at all.
        out = mf._flatten_sketch({"type": "sketch", "svg": "", "width": 1024, "height": 768})
        self.assertEqual(kinds(out), ["text"])

    def test_decision_C_composite_is_never_sent_to_the_model(self):
        # Regression guard for Entscheidung C (22.06.2026): even if a block
        # carries a composite (raw, pre-strip shape), the flattener must emit
        # exactly ONE image — the marked single view (rendered_png) — and never
        # the composite. If a future change re-wires composite-to-model, this
        # fails loudly.
        out = mf._flatten_sketch(
            {
                "type": "sketch",
                "svg": "<svg><path d='M1 1'/></svg>",
                "rendered_png": IMG,
                "composite": OTHER_IMG,
                "has_strokes": True,
                "view_name": "perspective",
                "width": 1000,
                "height": 750,
            }
        )
        imgs = images(out)
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0]["source"]["data"], "QUJD")  # rendered_png, not composite
        self.assertNotIn(OTHER_IMG["data"], [i["source"]["data"] for i in imgs])

    def test_svg_markup_is_never_forwarded_to_the_model(self):
        out = mf._flatten_sketch(
            {
                "type": "sketch",
                "svg": "<svg><path d='M9 9 L8 8'/></svg>",
                "rendered_png": IMG,
                "width": 800,
                "height": 600,
            }
        )
        for t in texts(out):
            self.assertNotIn("<path", t)
            self.assertNotIn("<svg", t)


class TestFlattenPluginBlocks(unittest.TestCase):
    def test_sketch_block_is_routed_through_flatten_sketch(self):
        out = mf._flatten_plugin_blocks(
            [{"type": "sketch", "svg": "", "rendered_png": IMG, "width": 10, "height": 10}]
        )
        self.assertEqual(kinds(out), ["text", "image"])

    def test_plain_text_block_passes_through_unchanged(self):
        block = {"type": "text", "text": "hallo"}
        self.assertEqual(mf._flatten_plugin_blocks([block]), [block])

    def test_image_without_origin_passes_through_unchanged(self):
        block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "Z"}}
        self.assertEqual(mf._flatten_plugin_blocks([block]), [block])

    def test_image_origin_metadata_is_stripped(self):
        block = {
            "type": "image",
            "origin": "viewport",  # plugin-only metadata the API rejects
            "source": {"type": "base64", "media_type": "image/png", "data": "Z"},
        }
        out = mf._flatten_plugin_blocks([block])
        self.assertEqual(len(out), 1)
        self.assertNotIn("origin", out[0])
        self.assertEqual(out[0]["source"], block["source"])

    def test_selection_smoke(self):
        out = mf._flatten_plugin_blocks([{"type": "selection", "object_ids": ["a", "b"]}])
        self.assertTrue(out and out[0]["type"] == "text")
        self.assertIn("Ausgewählte Objekte", texts(out)[0])

    def test_point_pick_smoke(self):
        out = mf._flatten_plugin_blocks([{"type": "point_pick", "point": [1.0, 2.0, 3.0]}])
        self.assertIn("Gepickter Punkt", texts(out)[0])

    def test_point_pick_invalid_is_handled(self):
        out = mf._flatten_plugin_blocks([{"type": "point_pick", "point": []}])
        self.assertTrue(texts(out))  # produces a caption, does not crash

    def test_component_pick_group_smoke(self):
        # Two consecutive component_picks are grouped into one flatten call.
        out = mf._flatten_plugin_blocks(
            [
                {"type": "component_pick", "component_type": "edge", "component_index": 1},
                {"type": "component_pick", "component_type": "face", "component_index": 2},
            ]
        )
        self.assertTrue(any(b["type"] == "text" for b in out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
