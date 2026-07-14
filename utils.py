import streamlit as st
import html as html_lib
import requests, hashlib, hmac, time, re, unicodedata, ftfy, os, psycopg2
from difflib import SequenceMatcher
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
def get_connection():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    con = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    con.autocommit = True
    return con

def set_db():
    con = get_connection()
    cur = con.cursor()
    # Accounts
    cur.execute("""
    CREATE TABLE IF NOT EXISTS account (
        client_id TEXT PRIMARY KEY,
        password  TEXT NOT NULL,
        type      TEXT NOT NULL DEFAULT 'F'
    );
    """)
    # Token balances
    cur.execute("""
    CREATE TABLE IF NOT EXISTS token (
        client_id TEXT PRIMARY KEY REFERENCES account(client_id),
        available INTEGER NOT NULL DEFAULT 0,
        used      INTEGER NOT NULL DEFAULT 0
    );
    """)
    # Blacklist
    cur.execute("""
    CREATE TABLE IF NOT EXISTS blacklist (
        word   TEXT PRIMARY KEY,
        status TEXT NOT NULL
               CHECK (status IN ('pending','approved'))
               DEFAULT 'pending'
    );
    """)
    # Censor log
    cur.execute("""
    CREATE TABLE IF NOT EXISTS censor_log (
        id            SERIAL PRIMARY KEY,
        client_id     TEXT    NOT NULL REFERENCES account(client_id),
        original_word TEXT    NOT NULL,
        event_ts      BIGINT  NOT NULL
    );
    """)
    # Lockouts
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lockout (
        client_id TEXT PRIMARY KEY,
        lock_ts   BIGINT NOT NULL DEFAULT 0
    );
    """)
    # Submissions
    cur.execute("""
    CREATE TABLE IF NOT EXISTS submission (
        id         SERIAL PRIMARY KEY,
        client_id  TEXT    NOT NULL REFERENCES account(client_id),
        original   TEXT    NOT NULL,
        corrected  TEXT    NOT NULL,
        error      INTEGER NOT NULL,
        event_ts   BIGINT  NOT NULL
    );
    """)
    # Upgrade requests
    cur.execute("""
    CREATE TABLE IF NOT EXISTS upgrade (
        client_id TEXT PRIMARY KEY REFERENCES account(client_id),
        req_ts    BIGINT NOT NULL
    );
    """)
    # Documents/files
    cur.execute("""
    CREATE TABLE IF NOT EXISTS file (
      file_id     SERIAL PRIMARY KEY,
      owner       TEXT NOT NULL REFERENCES account(client_id),
      title       TEXT,
      content     TEXT NOT NULL DEFAULT '',
      created_ts  BIGINT NOT NULL
    );
    """)
    # Collaborators link table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS collaborator (
      file_id    INTEGER NOT NULL REFERENCES file(file_id),
      client_id  TEXT    NOT NULL REFERENCES account(client_id),
      role       TEXT    NOT NULL DEFAULT 'edit',
      PRIMARY KEY (file_id, client_id)
    );
    """)
    # Pending invites
    cur.execute("""
    CREATE TABLE IF NOT EXISTS invite (
      invite_id     SERIAL PRIMARY KEY,
      file_id       INTEGER NOT NULL REFERENCES file(file_id),
      inviter       TEXT    NOT NULL REFERENCES account(client_id),
      invitee       TEXT    NOT NULL REFERENCES account(client_id),
      status        TEXT    NOT NULL CHECK (status IN ('pending','accepted','rejected')),
      requested_ts  BIGINT NOT NULL
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS complaint (
        complaint_id  SERIAL PRIMARY KEY,
        file_id      INTEGER NOT NULL REFERENCES file(file_id),
        complainant  TEXT NOT NULL REFERENCES account(client_id),
        complained   TEXT NOT NULL REFERENCES account(client_id),
        description  TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('pending', 'resolved')) DEFAULT 'pending',
        created_ts   BIGINT NOT NULL
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS complaint_response (
        response_id  SERIAL PRIMARY KEY,
        complaint_id INTEGER NOT NULL REFERENCES complaint(complaint_id),
        client_id    TEXT NOT NULL REFERENCES account(client_id),
        response     TEXT NOT NULL,
        created_ts   BIGINT NOT NULL
    );
    """)
    con.commit()
    con.close()

# Load login page
def get_page():
    return st.query_params.get("page", "login")

# Redirect to specified page
def set_page(page):
    st.query_params["page"] = page
    st.rerun()

# Hash user password with PBKDF2 and a per-user salt, stored as "salt$digest"
def hash_word(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 100_000
    ).hex()
    return f"{salt}${digest}"

def verify_password(password, stored):
    salt = stored.split("$", 1)[0]
    return hmac.compare_digest(hash_word(password, salt), stored)

# Add user client_id and password to database
def add_user(client_id, user_type, password):
    con = get_connection()
    cur = con.cursor()
    hash_password = hash_word(password)
    try:
        cur.execute(
            "INSERT INTO account (client_id, password, type) VALUES (%s, %s, %s)",
            (client_id, hash_password, user_type)
        )
        con.commit()
        return True
    except psycopg2.IntegrityError:
        # duplicate primary key or other constraint failure
        return False
    finally:
        con.close()

def search_user(client_id, password):
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT type, password FROM account WHERE client_id = %s",
        (client_id,)
    )
    row = cur.fetchone()
    con.close()
    if not row or not verify_password(password, row["password"]):
        return None
    return row["type"]

# Logout user session and redirect to login page
def logout_user():
    st.session_state['auth_stat'] = None
    st.session_state['client_id'] = None
    st.session_state['name'] = None
    st.session_state['type'] = None
    st.session_state['complaints_checked'] = False
    st.session_state['corrected_text'] = None
    st.session_state['rendered_html'] = None
    st.session_state['can_download'] = None
    st.session_state['user_input'] = None
    set_page("login")

def free_to_paid(client_id):
    con = get_connection()
    cur = con.cursor()
    try:
        # 1) mark them Paid
        cur.execute(
            "UPDATE account SET type = 'P' WHERE client_id = %s",
            (client_id,)
        )
        # 2) create their token row
        cur.execute(
            "INSERT INTO token (client_id, available, used) VALUES (%s, %s, %s)",
            (client_id, 0, 0)
        )
        con.commit()
    finally:
        con.close()

def free_to_super(client_id):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute(
            "UPDATE account SET type = 'S' WHERE client_id = %s",
            (client_id,)
        )
        con.commit()
    finally:
        con.close()

def request_free_to_paid(client_id) -> bool:
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO upgrade (client_id, req_ts) VALUES (%s, %s)",
            (client_id, int(time.time()))
        )
        con.commit()
        return True
    except psycopg2.IntegrityError:
        return False
    finally:
        con.close()

def get_token(client_id: str) -> tuple[int,int]:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT available, used FROM token WHERE client_id = %s",
        (client_id,)
    )
    row = cur.fetchone()
    con.close()

    if not row:
        return (0, 0)

    # If using RealDictCursor, row will be a dict:
    if isinstance(row, dict):
        return (row["available"], row["used"])
    # Otherwise it's a tuple
    return row

def update_token(client_id: str, available: int, used: int) -> None:
    con = get_connection()
    cur = con.cursor()
    # upsert the row if it doesn't exist yet
    cur.execute(
        """
        INSERT INTO token (client_id)
             VALUES (%s)
        ON CONFLICT (client_id) DO NOTHING
        """,
        (client_id,)
    )
    # check current balance
    cur.execute(
        "SELECT available FROM token WHERE client_id = %s",
        (client_id,)
    )
    row = cur.fetchone()
    current_available = row["available"] if row else 0
    # ensure new balance won't be negative
    if current_available + available < 0:
        con.close()
        raise ValueError(f"Cannot deduct {abs(available)} tokens: only {current_available} available")
    # apply the delta
    cur.execute(
        """
        UPDATE token
           SET available = available + %s,
               used      = used      + %s
         WHERE client_id = %s
        """,
        (available, used, client_id)
    )
    con.commit()
    con.close()

def show_paid_user_metrics(client_id):
    available, used = get_token(client_id)
    corrections = count_correction(client_id)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Available Tokens", available)
    with c2:
        st.metric("Used Tokens", used)
    with c3:
        st.metric("Corrections", corrections)

def count_price(orig: str, final: str) -> int:
    orig = normalize_punctuation(orig)
    final = normalize_punctuation(final)
    a = orig.split()
    b = final.split()
    s = SequenceMatcher(None, a, b)
    cost = 0
    for tag, i1, i2, j1, j2 in s.get_opcodes():
        if tag != "equal":
            if tag == "delete":
                cost += (i2 - i1)
            else:  # "replace" or "insert"
                cost += (j2 - j1)
    return cost

def self_correct_cost(orig: str, final: str) -> int:
    orig = normalize_punctuation(orig)
    final = normalize_punctuation(final)
    a = orig.split()
    b = final.split()
    s = SequenceMatcher(None, a, b)
    cost = 0
    for tag, i1, i2, j1, j2 in s.get_opcodes():
        if tag != "equal":
            if tag == "delete":
                cost += (i2 - i1)
            else:
                cost += (j2 - j1)
    return (cost + 1) // 2

def get_lockout(client_id: str) -> int:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT lock_ts FROM lockout WHERE client_id = %s",
        (client_id,)
    )
    row = cur.fetchone()
    con.close()

    if not row:
        return 0
    # RealDictCursor returns a dict
    if isinstance(row, dict):
        return row["lock_ts"]
    # otherwise a tuple
    return row[0]

def set_lockout(client_id: str, duration: int) -> None:
    lock_time = int(time.time()) + duration
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO lockout (client_id, lock_ts)
             VALUES (%s, %s)
        ON CONFLICT (client_id) DO UPDATE
          SET lock_ts = EXCLUDED.lock_ts
        """,
        (client_id, lock_time)
    )
    con.commit()
    con.close()

def remove_lockout(client_id: str) -> None:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "DELETE FROM lockout WHERE client_id = %s",
        (client_id,)
    )
    con.commit()
    con.close()

def get_submission(client_id: str) -> list[tuple]:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT original, corrected, error, event_ts
          FROM submission
         WHERE client_id = %s
      ORDER BY event_ts DESC
        """,
        (client_id,)
    )
    rows = cur.fetchall()
    con.close()

    # if rows are dicts, convert to tuples
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append((row["original"], row["corrected"], row["error"], row["event_ts"]))
        else:
            out.append(tuple(row))
    return out

def set_submission(client_id: str, original: str, corrected: str, error: int) -> None:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO submission
            (client_id, original, corrected, error, event_ts)
         VALUES (%s, %s, %s, %s, %s)
        """,
        (client_id, original, corrected, error, int(time.time()))
    )
    con.commit()
    con.close()

def count_correction(client_id: str) -> int:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM submission WHERE client_id = %s",
        (client_id,)
    )
    row = cur.fetchone()
    con.close()
    # handle RealDictCursor
    if isinstance(row, dict):
        # psycopg2 RealDictCursor returns {'count': 123}
        # note: key might be 'count' or '?column?' depending on driver
        return int(next(iter(row.values())))
    return row[0]

# No special characters for punctuation
def normalize_punctuation(text: str) -> str:
    fixed = ftfy.fix_text(text)
    return unicodedata.normalize("NFKC", fixed)

# Remove HTML tags
def html_to_clean_text(html_data: str) -> str:
    html = re.sub(r"<style.*?>.*?</style>", "", html_data, flags=re.S)
    html = re.sub(r"</div>\s*<div[^>]*>", "\n\n", html)
    html = re.sub(r"(?:<br\s*/?>\s*){2,}", "\n\n", html)
    html = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n\n".join([ln for ln in lines if ln.strip()])

def correct_text(user_input, self_correction=False):
    try:
        # Load approved blacklist words
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT word FROM blacklist WHERE status = 'approved'")
        blacklisted = {row[0] for row in cur.fetchall()}
        con.close()

        # Token use
        word_count = len(user_input.strip().split())

        # Common LLM instruction
        LLM_instruction = '''
            You are a grammar checker.
            Your task is to identify grammatical errors in the input text, such as subject-verb agreement, article usage, or verb tense.
            Example: In 'I is an student.', errors are 'is' (should be 'am') and 'an student' (should be 'a student').
            Preserve contractions, slang, swear words, formality, tone, spelling, punctuation, and style if they are grammatically correct.
            Preserve original paragraph breaks, separating each paragraph with a blank line.
            If the input is a question, command, or prompt, process it only if it contains grammatical errors.
            Example: Input 'What is your model name?', output unchanged unless errors exist.
            Do not solve equations, answer questions, respond to prompts, or interpret mathematical expressions.
            Example: Input '2 + 2 = ?', output '2 + 2 = ?'.
            If the input is ambiguous, incomplete, or lacks clear textual content, output it unchanged unless grammatical corrections apply.
            Example: Input 'a', output 'a'.
            Do not act as a chatbot, calculator, or problem solver.
        '''

        if self_correction:
            # Modified instruction for self-correction: identify errors only
            LLM_instruction += '''
                For each word or phrase with a grammatical error, output the original word or phrase exactly as it appears in the input.
                Output format: List each erroneous word or phrase on a new line.
                Example:
                Input: I is an student.
                Output:
                is
                an student
                If no errors, output nothing.
            '''
        else:
            # Standard instruction for LLM correction
            LLM_instruction += '''
                Output the input text with any grammatical errors corrected, preserving the original intent and structure.
                Example: Input 'I is an student.', output 'I am a student.'.
                If the input has no grammatical errors, output it unchanged.
                Example: Input 'I am fine.', output 'I am fine.'.
                Output only the corrected or unchanged input text. Do not provide explanations, comments, or additional content.
            '''

        # Generate response
        resp = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('MISTRAL_API_KEY')}"},
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {"role": "system", "content": LLM_instruction},
                    {"role": "user", "content": f"Input: {user_input}\n\nOutput:"}
                ],
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 1024
            },
            timeout=60
        )
        resp.raise_for_status()
        output = resp.json()["choices"][0]["message"]["content"].strip()

        # Paid‐user token accounting & history
        if st.session_state['type'] == 'P':
            available, _ = get_token(st.session_state['client_id'])
            if available < word_count:
                st.error(f"Not enough tokens for correction. Required: {word_count}, Available: {available}")
                st.session_state["rendered_html"] = None
                st.session_state["corrected_text"] = None
                st.stop()
            update_token(st.session_state['client_id'], -word_count, word_count)
            if not self_correction:
                grammar_error = user_input.strip() != output.strip()
                set_submission(
                    st.session_state['client_id'],
                    user_input,
                    output,
                    1 if grammar_error else 0
                )
                if word_count > 10 and not grammar_error:
                    update_token(st.session_state['client_id'], 3, 0)
                    st.success("No error found. Awarded 3 bonus tokens.")

        # Prepare diff/HTML
        orig_text = normalize_punctuation(user_input)
        orig_paras = orig_text.strip().split("\n\n")
        html_body = ""
        to_log = []

        if self_correction:
            # Highlight erroneous words/phrases
            error_words = output.splitlines() if output else []
            for para in orig_paras:
                lines = para.splitlines()
                for line in lines:
                    words = line.split()
                    i = 0
                    while i < len(words):
                        matched = False
                        for err in error_words:
                            err_len = len(err.split())
                            if i + err_len <= len(words):
                                phrase = " ".join(words[i:i + err_len])
                                if phrase == err:
                                    # Highlight the erroneous phrase
                                    phrase_esc = html_lib.escape(phrase, quote=True)
                                    html_body += (
                                        f'<span class="toggle" '
                                        f'data-original="{phrase_esc}" '
                                        f'style="background:#2EBD2E; border-radius:8px; '
                                        f'padding:4px; display:inline-block; cursor:pointer; '
                                        f'font-size:16px; color:white;">'
                                        f'{phrase_esc}</span> '
                                    )
                                    i += err_len
                                    matched = True
                                    break
                        if not matched:
                            # Non-erroneous word
                            word = words[i]
                            # Check for blacklisted words
                            clean = re.sub(r'\W+', '', word).lower()
                            if clean in blacklisted:
                                to_log.append(clean)
                                word = "***"
                            html_body += html_lib.escape(word, quote=True) + " "
                            i += 1
                    html_body += "<br>"
                html_body += "<br>"
            st.session_state["corrected_text"] = user_input  # Keep original for editing
        else:
            # Standard LLM correction
            corr_text = normalize_punctuation(output)
            corr_paras = corr_text.strip().split("\n\n")
            for orig_para, corr_para in zip(orig_paras, corr_paras):
                orig_lines = orig_para.splitlines()
                corr_lines = corr_para.splitlines()
                for o_line, c_line in zip(orig_lines, corr_lines):
                    o_words = o_line.split()
                    c_words = c_line.split()
                    diff = SequenceMatcher(None, o_words, c_words)
                    for tag, o1, o2, c1, c2 in diff.get_opcodes():
                        if tag == 'equal':
                            html_body += " ".join(c_words[c1:c2]) + " "
                        else:
                            segment = " ".join(c_words[c1:c2])
                            original = " ".join(o_words[o1:o2])
                            # log blacklisted
                            for w in c_words[c1:c2]:
                                clean = re.sub(r'\W+', '', w).lower()
                                if clean in blacklisted:
                                    to_log.append(clean)
                                    segment = segment.replace(w, "***")
                            orig_esc = html_lib.escape(original, quote=True)
                            seg_esc = html_lib.escape(segment, quote=True)
                            html_body += (
                                f'<span class="toggle" '
                                f'data-original="{orig_esc}" '
                                f'data-corrected="{seg_esc}" '
                                f'style="background:#2EBD2E; border-radius:8px; '
                                f'padding:4px; display:inline-block; cursor:pointer; '
                                f'font-size:16px; color:white;">'
                                f'{seg_esc}</span> '
                            )
                    html_body += "<br>"
                html_body += "<br>"
            st.session_state["corrected_text"] = "\n\n".join(output.split("\n\n"))

        # Log any censored words
        if to_log:
            con = get_connection()
            cur = con.cursor()
            for w in set(to_log):
                cur.execute(
                    """
                    INSERT INTO censor_log (client_id, original_word, event_ts)
                         VALUES (%s, %s, %s)
                    """,
                    (st.session_state['client_id'], w, int(time.time()))
                )
            con.commit()
            con.close()

        # Finalize session state
        html_body = re.sub(r'(<br>\s*)+$', '', html_body)
        st.session_state["rendered_html"] = f"<div>{html_body}</div>"
        st.session_state["can_download"] = False
        if "original_input" not in st.session_state:
            st.session_state["original_input"] = user_input

    except Exception:
        st.error("❌ Failed to connect to the language model. Please try again.")
        st.stop()

def is_instruction_like(text: str) -> bool:
    words = text.strip().split()
    if len(words) >= 25:
        return False

    pattern = (
        r"(can you|fix|correct grammar|output only|do not explain|return it unchanged|fix (spelling|punctuation))"
    )
    return bool(re.search(pattern, text.lower()))

def render_blacklist_form():
    st.markdown("---")
    st.subheader("🚫 Suggest a word for blacklist")
    w = st.text_input("Enter a word to suggest")
    if st.button("Submit to Blacklist"):
        submit_blacklist_word(w.strip().lower())

def submit_blacklist_word(word: str):
    if not word:
        st.warning("Input can't be empty.")
        return

    con = get_connection()
    cur = con.cursor()
    # Try to insert; if it already exists, no-op
    cur.execute(
        """
        INSERT INTO blacklist (word, status)
             VALUES (%s, 'pending')
        ON CONFLICT (word) DO NOTHING
        """,
        (word,)
    )

    if cur.rowcount == 0:
        # rowcount==0 means the INSERT was skipped due to conflict
        st.info("This word has already been submitted.")
    else:
        con.commit()
        st.success("Submitted for review.")

    con.close()

#--- CSS Style for Corrected Text Box ---#
def wrap_scrollable(raw_html: str, max_height: int = 300) -> str:
    """
    Wrap `raw_html` in a scrollable div with a configurable max-height.
    """
    css = f"""
    <style>
    .scrollable {{
        background-color: #262730;
        border: 1px solid #1D751D;
        border-radius: 8px;
        padding: 8px;
        max-height: {max_height}px;
        overflow-y: auto;
        user-select: none;
    }}
    /* Chrome, Edge, Safari */
    .scrollable::-webkit-scrollbar {{
        width: 6px;
    }}
    .scrollable::-webkit-scrollbar-track {{
        background: #262730;
        border-radius: 3px;
    }}
    .scrollable::-webkit-scrollbar-thumb {{
        background-color: #7B7B81;
        border-radius: 3px;
    }}
    /* Firefox */
    .scrollable {{
        scrollbar-width: thin;
        scrollbar-color: #7B7B81 #262730;
    }}
    </style>
    """
    return f"{css}<div class='scrollable'>{raw_html}</div>"

def create_file(owner: str, title: str) -> int:
    """
    Create a new file record and add the owner as its first collaborator.
    Returns the new file_id.
    """
    con = get_connection()
    cur = con.cursor()
    created_ts = int(time.time())
    # 1) Insert into file, returning its ID
    cur.execute(
        """
        INSERT INTO file (owner, title, created_ts)
             VALUES (%s,   %s,    %s)
        RETURNING file_id
        """,
        (owner, title, created_ts)
    )
    file_id = cur.fetchone()["file_id"]
    # 2) Add owner as collaborator
    cur.execute(
        """
        INSERT INTO collaborator (file_id, client_id)
             VALUES (%s,       %s)
        ON CONFLICT DO NOTHING
        """,
        (file_id, owner)
    )
    con.commit()
    con.close()
    return file_id

def invite_user(file_id: int, inviter: str, invitee: str) -> bool:
    """
    Send a collaboration invite.
    Returns False if the invitee doesn’t exist or is already a collaborator/invited;
    True if the invite was created.
    """
    con = get_connection()
    cur = con.cursor()

    # (a) ensure the invitee exists
    cur.execute("SELECT 1 FROM account WHERE client_id = %s", (invitee,))
    if not cur.fetchone():
        con.close()
        return False

    # (b) no duplicate invite or existing collaborator
    cur.execute(
        "SELECT 1 FROM collaborator WHERE file_id = %s AND client_id = %s",
        (file_id, invitee)
    )
    if cur.fetchone():
        con.close()
        return False

    cur.execute(
        "SELECT 1 FROM invite "
        " WHERE file_id = %s AND invitee = %s AND status = 'pending'",
        (file_id, invitee)
    )
    if cur.fetchone():
        con.close()
        return False

    # (c) create the pending invite
    req_ts = int(time.time())
    cur.execute(
        """
        INSERT INTO invite (file_id, inviter, invitee, status, requested_ts)
             VALUES (%s,      %s,       %s,      'pending', %s)
        """,
        (file_id, inviter, invitee, req_ts)
    )

    con.commit()
    con.close()
    return True

def list_invites_for(user: str) -> list[dict]:
    """
    Returns all pending invites for `user`.
    Each dict has keys: invite_id, file_id, inviter, title, requested_ts.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
      SELECT i.invite_id, i.file_id, i.inviter, f.title, i.requested_ts
        FROM invite i
        JOIN file f ON f.file_id = i.file_id
       WHERE i.invitee = %s
         AND i.status = 'pending'
       ORDER BY i.requested_ts DESC
    """, (user,))
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        out.append({
            "invite_id":    r["invite_id"],
            "file_id":      r["file_id"],
            "inviter":      r["inviter"],
            "title":        r["title"],
            "requested_ts": r["requested_ts"],
        })
    return out

def respond_invite(invite_id: int, accept: bool) -> None:
    """
    Mark the given invite as accepted or rejected.
    If accepted, also add to collaborator table.
    """
    con = get_connection()
    cur = con.cursor()
    status = "accepted" if accept else "rejected"

    # 1) update invite status
    cur.execute("UPDATE invite SET status = %s WHERE invite_id = %s", (status, invite_id))

    # 2) if accepted, add to collaborators
    if accept:
        cur.execute("SELECT file_id, invitee FROM invite WHERE invite_id = %s", (invite_id,))
        row = cur.fetchone()
        if row:
            fid  = row["file_id"]
            user = row["invitee"]
            cur.execute(
                """
                INSERT INTO collaborator (file_id, client_id)
                     VALUES (%s,       %s)
                ON CONFLICT DO NOTHING
                """,
                (fid, user)
            )

    con.commit()
    con.close()

def list_files_for(user: str) -> list[dict]:
    """
    Returns every file the user owns or is a collaborator on.
    Each dict: file_id, title, owner, created_ts.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
      SELECT f.file_id, f.title, f.owner, f.created_ts
        FROM file f
        JOIN collaborator c ON c.file_id = f.file_id
       WHERE c.client_id = %s
       ORDER BY f.created_ts DESC
    """, (user,))
    rows = cur.fetchall()
    con.close()
    return [
        {"file_id": r["file_id"], "title": r["title"], "owner": r["owner"], "created_ts": r["created_ts"]}
        for r in rows
    ]

def update_file_content(file_id: int, content: str) -> None:
    """Overwrite the ‘content’ column for this file."""
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "UPDATE file SET content = %s WHERE file_id = %s",
        (content, file_id)
    )
    con.commit()
    con.close()

def get_file_content(file_id: int) -> str:
    """Fetch the latest content for this file, or empty string."""
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT content FROM file WHERE file_id = %s",
        (file_id,)
    )
    row = cur.fetchone()
    con.close()
    if not row:
        return ""
    return row["content"] if isinstance(row, dict) else row[0]

def submit_complaint(file_id: int, complainant: str, complained: str, description: str) -> bool:
    con = get_connection()
    cur = con.cursor()
    try:
        # Verify both users are collaborators
        cur.execute(
            """
            SELECT 1 FROM collaborator
            WHERE file_id = %s AND client_id = %s
            INTERSECT
            SELECT 1 FROM collaborator
            WHERE file_id = %s AND client_id = %s
            """,
            (file_id, complainant, file_id, complained)
        )
        if not cur.fetchone():
            return False
        # Prevent self-complaint
        if complainant == complained:
            return False
        # Insert complaint
        cur.execute(
            """
            INSERT INTO complaint (file_id, complainant, complained, description, status, created_ts)
            VALUES (%s, %s, %s, %s, 'pending', %s)
            """,
            (file_id, complainant, complained, description, int(time.time()))
        )
        con.commit()
        return True
    except psycopg2.IntegrityError:
        return False
    finally:
        con.close()

def get_complaint_paid(client_id: str) -> list[dict]:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT c.complaint_id, c.file_id, c.complainant, c.description, c.created_ts, f.title
        FROM complaint c
        JOIN file f ON c.file_id = f.file_id
        WHERE c.complained = %s AND c.status = 'pending'
        ORDER BY c.created_ts DESC
        """,
        (client_id,)
    )
    rows = cur.fetchall()
    con.close()
    return [
        {
            "complaint_id": r["complaint_id"],
            "file_id": r["file_id"],
            "complainant": r["complainant"],
            "description": r["description"],
            "created_ts": r["created_ts"],
            "title": r["title"]
        }
        for r in rows
    ]

def submit_complaint_response(complaint_id: int, client_id: str, response: str) -> bool:
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute(
            """
            INSERT INTO complaint_response (complaint_id, client_id, response, created_ts)
            VALUES (%s, %s, %s, %s)
            """,
            (complaint_id, client_id, response, int(time.time()))
        )
        con.commit()
        return True
    except psycopg2.IntegrityError:
        return False
    finally:
        con.close()

def get_complaint_super() -> list[dict]:
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT c.complaint_id, c.file_id, c.complainant, c.complained, c.description, c.created_ts, f.title
        FROM complaint c
        JOIN file f ON c.file_id = f.file_id
        WHERE c.status = 'pending'
        ORDER BY c.created_ts DESC
        """,
    )
    complaints = cur.fetchall()
    out = []
    for c in complaints:
        # Fetch responses
        cur.execute(
            """
            SELECT client_id, response, created_ts
            FROM complaint_response
            WHERE complaint_id = %s
            ORDER BY created_ts DESC
            """,
            (c["complaint_id"],)
        )
        responses = cur.fetchall()
        out.append({
            "complaint_id": c["complaint_id"],
            "file_id": c["file_id"],
            "complainant": c["complainant"],
            "complained": c["complained"],
            "description": c["description"],
            "created_ts": c["created_ts"],
            "title": c["title"],
            "responses": [
                {"client_id": r["client_id"], "response": r["response"], "created_ts": r["created_ts"]}
                for r in responses
            ]
        })
    con.close()
    return out

def handle_complaint():
    if st.session_state['type'] not in ('P', 'S'):
        st.session_state['complaints_checked'] = True
        return
    complaints = get_complaint_paid(st.session_state['name'])
    if not complaints:
        st.session_state['complaints_checked'] = True
        return
    st.session_state['complaints_checked'] = False
    st.markdown("You Have Pending Complaints")
    st.warning("You must respond to all complaints before proceeding.")
    for complaint in complaints:
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(complaint['created_ts']))
        with st.form(key=f"complaint_{complaint['complaint_id']}"):
            st.write(f"**Complaint from {complaint['complainant']}** on document '{complaint['title']}' at {ts}")
            st.write(f"**Description**: {complaint['description']}")
            response = st.text_area("Your Response", key=f"response_{complaint['complaint_id']}")
            submit = st.form_submit_button("Submit Response")
            if submit and response.strip():
                if submit_complaint_response(complaint['complaint_id'], st.session_state['name'], response):
                    st.success("Response submitted.")
                    st.rerun()
                else:
                    st.error("Failed to submit response.")
    st.stop()

def resolve_complaint(complaint_id: int, complainant_penalty: int, complained_penalty: int) -> tuple[bool, str]:
    con = get_connection()
    cur = con.cursor()
    try:
        # Fetch complainant and complained
        cur.execute(
            """
            SELECT complainant, complained
            FROM complaint
            WHERE complaint_id = %s
            """,
            (complaint_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Complaint not found"
        complainant = row["complainant"]
        complained = row["complained"]

        # Check token balances
        complainant_tokens, _ = get_token(complainant)
        complained_tokens, _ = get_token(complained)
        if complainant_tokens < complainant_penalty:
            return False, f"Complainant has only {complainant_tokens} tokens, cannot deduct {complainant_penalty}"
        if complained_tokens < complained_penalty:
            return False, f"Complained user has only {complained_tokens} tokens, cannot deduct {complained_penalty}"

        # Apply penalties
        if complainant_penalty > 0:
            update_token(complainant, -complainant_penalty, complainant_penalty)
        if complained_penalty > 0:
            update_token(complained, -complained_penalty, complained_penalty)

        # Mark complaint as resolved
        cur.execute(
            """
            UPDATE complaint
            SET status = 'resolved'
            WHERE complaint_id = %s
            """,
            (complaint_id,)
        )
        con.commit()
        return True, "Complaint resolved successfully"
    except Exception as e:
        return False, str(e)
    finally:
        con.close()