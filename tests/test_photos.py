"""Verify original photo handling without external calls or generated imagery."""
import io
import unittest
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

import main


class OriginalPhotoTests(unittest.TestCase):
    url = "https://net-storage-auto.tcccdn.com/storage/milannews.it/img_notizie/original.jpg"

    def download(self, data, content_type="image/jpeg"):
        response = MagicMock()
        response.headers = {"Content-Type": content_type}
        response.iter_content.return_value = [data]
        response.__enter__.return_value = response
        return response

    @staticmethod
    def image_bytes(fmt="JPEG", size=(600, 360)):
        output = io.BytesIO()
        Image.new("RGB", size, (120, 60, 30)).save(output, format=fmt)
        return output.getvalue()

    def test_original_jpeg_and_png_are_reencoded_as_valid_jpeg(self):
        for fmt, content_type in (("JPEG", "image/jpeg"), ("PNG", "image/png")):
            with self.subTest(format=fmt):
                original = self.image_bytes(fmt)
                with patch("main.requests.get", return_value=self.download(original, content_type)) as get:
                    photo = main.original_photo_bytes(self.url)
                get.assert_called_once_with(self.url, headers=main.HEADERS,
                                            timeout=main.HTTP_TIMEOUT, stream=True)
                self.assertIsInstance(photo, bytes)
                with Image.open(io.BytesIO(photo)) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.size, (600, 360))

    def test_large_original_is_resized_without_changing_aspect_ratio(self):
        with patch("main.requests.get", return_value=self.download(self.image_bytes(size=(3000, 1500)))):
            photo = main.original_photo_bytes(self.url)
        with Image.open(io.BytesIO(photo)) as image:
            self.assertEqual(image.size, (1600, 800))

    def test_absent_photo_requires_no_network(self):
        with patch("main.requests.get") as get:
            self.assertIsNone(main.original_photo_bytes(None))
        get.assert_not_called()

    def test_tiny_or_invalid_image_is_rejected_for_text_fallback(self):
        for data in (self.image_bytes(size=(100, 100)), b"not image data"):
            with self.subTest(data_length=len(data)):
                with patch("main.requests.get", return_value=self.download(data)):
                    self.assertIsNone(main.original_photo_bytes(self.url))

    def test_non_photo_content_is_rejected(self):
        for content_type in ("text/html", "image/svg+xml", "image/gif"):
            with self.subTest(content_type=content_type):
                with patch("main.requests.get", return_value=self.download(b"payload", content_type)):
                    self.assertIsNone(main.original_photo_bytes(self.url))

    def test_oversized_download_is_rejected(self):
        with patch("main.requests.get", return_value=self.download(b"x" * 12_000_001)):
            self.assertIsNone(main.original_photo_bytes(self.url))

    def test_network_failure_returns_no_photo(self):
        with patch("main.requests.get", side_effect=requests.ConnectionError("private proxy url")):
            self.assertIsNone(main.original_photo_bytes(self.url))

    def test_decompression_bomb_falls_back_to_text(self):
        with patch("main.requests.get", return_value=self.download(b"image")):
            with patch("main.Image.open", side_effect=Image.DecompressionBombError("too many pixels")):
                self.assertIsNone(main.original_photo_bytes(self.url))


if __name__ == "__main__":
    unittest.main()
