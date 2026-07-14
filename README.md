# LLM-Based Text Editor

A web app for grammar correction powered by a large language model. Users submit text, review highlighted corrections, and refine the result, with a token-based economy and collaborative editing built on top.

## Features

- **LLM grammar correction**: submitted text is checked by Mistral, with corrections shown as an inline diff you can edit before accepting
- **Live word count** while typing
- **Three user tiers**:
  - Free users can correct short texts (20 word limit, with a timeout penalty for going over)
  - Paid users spend tokens per correction and unlock downloads, submission history, self-correction mode, and collaboration
  - Super users moderate the word blacklist, approve paid-account requests, resolve complaints, and view censor logs
- **Collaborative editing**: paid users can save corrected text as a shared document, invite other users, and work toward an agreed-upon version together
- **Complaints system**: collaborators can file complaints against each other, which a super user resolves with token penalties

## Stack

- [Streamlit](https://streamlit.io/) frontend and server, including a custom bidirectional React component for the editable diff view
- PostgreSQL for accounts, tokens, documents, and moderation state
- [Mistral API](https://mistral.ai/) for corrections

## Running locally

1. Create a virtual environment and install dependencies:

   ```
   python -m venv venv
   venv/Scripts/activate      # Windows
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the project root:

   ```
   DATABASE_URL="postgresql://user:password@host/dbname"
   MISTRAL_API_KEY="your-key"
   ```

   Any empty PostgreSQL database works, the app creates its tables on first run.

3. Start the app:

   ```
   streamlit run app.py
   ```

## Deployment

The app runs on any host that supports a long-lived Python process. This instance is deployed on Render (free tier) with the database on Neon, so the first visit after a quiet period may take a minute while the service wakes up.

*Built as a software engineering course project, later cleaned up and deployed.*
