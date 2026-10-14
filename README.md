# AI Healthcare Call Summary

A Streamlit demonstration that summarizes sample call audio or staff-entered
notes and displays evidence-backed action items for human review.

## Features

- Process the included recorded-call samples.
- Receive a live microphone recording through the ASGI audio routes.
- Process staff notes without presenting them as verbatim patient statements.
- Optionally record local microphone audio after confirming consent; audio is
  transcribed by Deepgram and summarized by Groq.
- Send a verified action item to a department queue held in Streamlit session
  state. This is a local demo queue, not an integration with an external
  hospital system.

## Run locally

Install the dependencies and configure `GROQ_API_KEY` and `DEEPGRAM_API_KEY`
in a `.env` file beside `app.py`. Keep `.env` private; it is ignored by Git.

```bash
pip install -r requirements.txt
```

```text
GROQ_API_KEY="your-groq-api-key"
DEEPGRAM_API_KEY="your-deepgram-api-key"
```

Start the ASGI wrapper from the repository root so the audio routes are
available:

```bash
streamlit run asgi_app.py
```

## Deploy to Streamlit Community Cloud

1. Push the repository to GitHub, including `requirements.txt`, the audio
   samples, and the application source.
2. In Streamlit Community Cloud, create an app from the repository and select
   the deployment branch and `asgi_app.py` as the **Main file path**. The ASGI
   wrapper is required for the live recorder routes. Bundled sample MP3 audio
   is embedded in the player so playback does not depend on custom audio routes.
3. In **Advanced settings**, select Python 3.12 and add the following to
   **Secrets**, replacing the placeholders with your own keys:

   ```toml
   GROQ_API_KEY = "your-groq-api-key"
   DEEPGRAM_API_KEY = "your-deepgram-api-key"
   ```

4. Deploy the app and review the Cloud logs if startup or API calls fail.

Never commit real credentials or `.streamlit/secrets.toml`. The application
reads secrets from Streamlit Community Cloud or, locally, from `.env`.

## Validation

Run the mocked local tests with:

```bash
python -m unittest test_staff_notes test_department_queue test_settings
```

Provider API tests in `test_keys.py` make real external requests and are not
part of this test command.

## Data and safety limitations

This is a demonstration, not a clinical or production system. Do not upload
patient-identifiable or otherwise sensitive health information. Audio and
transcripts submitted for summarization are sent to Deepgram and Groq; confirm
appropriate consent, privacy, and vendor requirements before using real data.
The department queue is session-only and is not durable across sessions or
server restarts. Sending an item does not notify a real department.
