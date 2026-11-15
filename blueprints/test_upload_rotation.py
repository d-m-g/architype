"""Uploads: a sideways plan is turned upright and read again; a non-plan is refused; no key lets it through. No model call."""
import asyncio
import io
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["BLUEPRINTS_CACHE"] = tempfile.mkdtemp()  # before the app is imported: uploads land here, not in .cache
os.environ.pop("BLUEPRINTS_PASSWORD_HASH", None)

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from blueprints import api  # noqa: E402
from blueprints.brain import vision  # noqa: E402
from blueprints.schemas import Page, Scene  # noqa: E402

PAGE = Page(image="x", width=1, height=1)
SIDEWAYS = Scene(path="x", builder=vision.BUILDER + vision.TURN + "90", tier="detect", covers=["room"], pages=[PAGE])
UPRIGHT = Scene(path="x", builder=vision.BUILDER, tier="detect", covers=["room"], pages=[PAGE])
BRIDGE = Scene(path="x", builder=vision.BUILDER + vision.NOT_A_PLAN + "bridge elevation", tier="detect", covers=[], pages=[PAGE])


def png(width, height):
    data = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(data, "PNG")
    return data.getvalue()


def reader(reads):
    """The vision reader as it would answer: a 40 x 20 image is sideways, 50 x 50 is a bridge, anything else upright."""
    def read(path):
        with Image.open(vision.UPLOADS / path.split("/")[1]) as image:
            reads.append(image.size)
        return {(40, 20): SIDEWAYS, (50, 50): BRIDGE}.get(reads[-1], UPRIGHT), True
    return read


def upload(data, name, read):
    async def go():
        with patch("blueprints.brain.vision.read_scene", side_effect=read), patch("blueprints.brain.vision.forget"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t") as client:
                reply = await client.post("/api/upload", params={"filename": name}, content=data)
                return reply.status_code, reply.json()
    return asyncio.run(go())


class UploadRotation(unittest.TestCase):
    def test_a_sideways_plan_is_turned_upright_read_again_and_reported(self):
        reads = []
        status, body = upload(png(40, 20), "side.png", reader(reads))
        self.assertEqual((status, body["rotated"]), (200, 90))
        self.assertEqual(reads, [(40, 20), (20, 40)])  # the first read said turn it; the second reads it upright
        with Image.open(vision.UPLOADS / body["path"].split("/")[1]) as image:
            self.assertEqual(image.size, (20, 40))
        reads.clear()
        status, again = upload(png(40, 20), "side.png", reader(reads))  # the same file again: kept turned, not re-read
        self.assertEqual((status, again["path"], again["rotated"], reads), (200, body["path"], True, []))

    def test_an_upright_plan_is_read_once_and_left_alone(self):
        reads = []
        status, body = upload(png(30, 40), "up.png", reader(reads))
        self.assertEqual((status, "rotated" in body, reads), (200, False, [(30, 40)]))

    def test_a_non_plan_is_refused_and_not_kept(self):
        status, body = upload(png(50, 50), "bridge.png", reader([]))
        self.assertEqual(status, 422)
        self.assertIn("Not a floor plan: this looks like a bridge elevation", body["detail"])
        kept = [path for path in vision.UPLOADS.glob("*.png") if Image.open(path).size == (50, 50)]
        self.assertEqual(kept, [])  # refused files are deleted

    def test_without_the_reader_the_upload_goes_through_unchanged(self):
        def no_key(path):
            raise NotImplementedError("no key")
        status, body = upload(png(41, 20), "nokey.png", no_key)
        self.assertEqual((status, "rotated" in body), (200, False))


if __name__ == "__main__":
    unittest.main()
