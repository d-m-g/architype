"""Uploads: a small look says whether it is a floor plan and which way up; a turned plan is stored upright, a non-plan
refused, and with no reader the file goes through. The model reply is canned: no model call."""
import asyncio
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["BLUEPRINTS_CACHE"] = tempfile.mkdtemp()  # before the app is imported: uploads land here, not in .cache
os.environ.pop("BLUEPRINTS_PASSWORD_HASH", None)

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from blueprints import api, pipeline  # noqa: E402
from blueprints.brain import vision  # noqa: E402


def png(width, height, corner=(255, 0, 0)):
    """A white image with one coloured pixel top-left, to see where a turn puts it."""
    image = Image.new("RGB", (width, height), "white")
    image.putpixel((0, 0), corner)
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


class Reader:
    """Stands in for the vision model: answers the orientation sheet with a canned reply and counts the calls."""

    def __init__(self, reply):
        self.reply, self.calls = reply, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls += 1
        sheet = request["messages"][0]["content"][0]["image_url"]["url"]
        assert sheet.startswith("data:image/jpeg;base64,") and "A, B, C and D" in request["messages"][0]["content"][1]["text"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
                               usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=20))


def upload(data, name, reader=None):
    async def go():
        environment = {"GEMINI_API_KEY": "test"} if reader else {}
        with patch.dict(os.environ, environment), patch("openai.OpenAI", return_value=reader):
            if not reader:
                os.environ.pop("GEMINI_API_KEY", None)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t") as client:
                reply = await client.post("/api/upload", params={"filename": name}, content=data)
                return reply.status_code, reply.json()
    return asyncio.run(go())


def stored(body):
    return vision.UPLOADS / body["path"].split("/")[1]


class UploadRotation(unittest.TestCase):
    def test_an_upside_down_plan_is_stored_upright_and_asked_about_once(self):
        reader = Reader('{"is_floor_plan": true, "drawing_kind": "floor plan", "upright": "C"}')  # C: turned 180
        status, body = upload(png(40, 20), "down.png", reader)
        self.assertEqual((status, body["rotated"], reader.calls), (200, 180, 1))
        with Image.open(stored(body)) as image:
            self.assertEqual((image.size, image.getpixel((39, 19))), ((40, 20), (255, 0, 0)))  # the corner went top-left -> bottom-right
        status, again = upload(png(40, 20), "down.png", reader)  # the same file again: still upright, not asked again, not turned twice
        self.assertEqual((status, again["rotated"], reader.calls), (200, 180, 1))
        with Image.open(stored(again)) as image:
            self.assertEqual(image.getpixel((39, 19)), (255, 0, 0))
        steps = vision.glance_steps(body["path"])
        self.assertEqual([(s.step, s.in_tokens) for s in steps], [("orientation", 1200)])
        self.assertGreater(steps[0].usd, 0)

    def test_a_sideways_plan_is_turned_a_quarter(self):
        status, body = upload(png(44, 20), "side.png", Reader('{"is_floor_plan": true, "drawing_kind": "floor plan", "upright": "B"}'))
        self.assertEqual((status, body["rotated"]), (200, 90))
        with Image.open(stored(body)) as image:
            self.assertEqual((image.size, image.getpixel((19, 0))), ((20, 44), (255, 0, 0)))

    def test_a_plan_uploaded_before_this_check_existed_is_checked_and_its_old_scenes_dropped(self):
        data = png(46, 20)
        _, body = upload(data, "old.png")  # no reader: stored as it came, unchecked
        cached = pipeline.scene_file(body["path"])
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text("{}")
        status, body = upload(data, "old.png", Reader('{"is_floor_plan": true, "drawing_kind": "floor plan", "upright": "C"}'))
        self.assertEqual((status, body["rotated"], cached.exists()), (200, 180, False))

    def test_an_upright_plan_is_left_alone(self):
        reader = Reader('{"is_floor_plan": true, "drawing_kind": "floor plan", "upright": "A"}')
        status, body = upload(png(30, 40), "up.png", reader)
        self.assertEqual((status, "rotated" in body, reader.calls), (200, False, 1))
        self.assertEqual(stored(body).read_bytes(), png(30, 40))

    def test_a_non_plan_is_refused_and_not_kept(self):
        reader = Reader('{"is_floor_plan": false, "drawing_kind": "bridge elevation", "upright": "A"}')
        for _ in range(2):  # refused again without asking again
            status, body = upload(png(50, 50), "bridge.png", reader)
            self.assertEqual(status, 422)
            self.assertIn("Not a floor plan: this looks like a bridge elevation", body["detail"])
        self.assertEqual(reader.calls, 1)
        self.assertEqual([p for p in vision.UPLOADS.glob("*.png") if Image.open(p).size == (50, 50)], [])

    def test_without_the_reader_or_with_a_broken_reply_the_upload_goes_through_unchanged(self):
        self.assertEqual(upload(png(41, 20), "nokey.png")[0], 200)
        status, body = upload(png(42, 20), "broken.png", Reader("not json"))
        self.assertEqual((status, "rotated" in body), (200, False))
        self.assertEqual(stored(body).read_bytes(), png(42, 20))


if __name__ == "__main__":
    unittest.main()
