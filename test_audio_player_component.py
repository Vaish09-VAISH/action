import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from audio_player_component import audio_file_data_uri


class AudioFileDataUriTests(unittest.TestCase):
    def tearDown(self):
        audio_file_data_uri.clear()

    def test_mp3_is_embedded_as_playable_mpeg_data_uri(self):
        audio_path = (
            Path(__file__).resolve().parent
            / "audio"
            / "audio"
            / "01_scheduling.mp3"
        )
        data_uri = audio_file_data_uri(str(audio_path))

        prefix = "data:audio/mpeg;base64,"
        self.assertTrue(data_uri.startswith(prefix))
        decoded_audio = base64.b64decode(data_uri.removeprefix(prefix))
        self.assertEqual(decoded_audio, audio_path.read_bytes())

    def test_missing_or_non_mp3_file_is_rejected(self):
        with TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "missing.wav"
            with self.assertRaises(FileNotFoundError):
                audio_file_data_uri(str(audio_path))


if __name__ == "__main__":
    unittest.main()
