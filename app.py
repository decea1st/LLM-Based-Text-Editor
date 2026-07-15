from streamlit_html_viewer.streamlit_html_viewer import streamlit_html_viewer as html_viewer
from utils import *
import streamlit as st
import streamlit.components.v1 as components
import json

st.set_page_config(page_title="LLM-Based Text Editor")

# Initialize session state keys
for key in ['auth_stat', 'name', 'type', 'client_id', 'corrected_text', 'rendered_html', 'can_download', 'user_input']:
    st.session_state.setdefault(key, None)

st.session_state.setdefault('complaints_checked', False)

if st.session_state.get("client_id") is None:
    ip = st.context.ip_address
    if not ip:
        ip = st.query_params.get("client_ip", [None])[0]
    st.session_state["client_id"] = ip or ""
client_id = st.session_state["client_id"]

st.markdown(
    """
    <style>
      label[data-testid="stWidgetLabel"][disabled] {
        display: none !important;
      }
    </style>
    """,
    unsafe_allow_html=True
)

@st.fragment
def render_download_button():
    st.download_button(
        label="📥 Download .txt File",
        data=st.session_state["corrected_text"],
        file_name="corrected_text.txt",
        mime="text/plain"
    )

if not st.session_state.get("_db_initialized"):
    set_db()
    st.session_state["_db_initialized"] = True
page = get_page()

if page == "login":
    st.title("Login")

    if st.session_state.pop("signup_success", False):
        st.success("Signup successful! Please log in.")

    with st.form("login_form"):
        name = st.text_input("Username")
        password = st.text_input("Password", type="password")

        submitted_login = st.form_submit_button("Login")
        submitted_signup = st.form_submit_button("Create A New Account")
    
    if submitted_login:
        name = name.strip()
        user_type = search_user(name, password)
        if user_type:
            # ban free users by IP or by name if no IP
            lock_key = client_id if client_id else name
            if user_type == 'F':
                lock_time = get_lockout(lock_key)
                if lock_time > time.time():
                    remaining = lock_time - time.time()
                    st.error(f"You have been timed out for {remaining:.0f}s")
                    st.stop()

            # at this point, login OK
            st.session_state['name']        = name
            st.session_state['client_id']   = st.session_state['name']
            st.session_state['auth_stat']   = True
            st.session_state['type']        = user_type
            set_page("main")
        else:
            st.session_state['auth_stat'] = False
            st.error("Incorrect username or password")
    
    if submitted_signup:
        set_page("signup")
    
    if st.session_state['auth_stat'] is None:
        st.warning("Please enter username and password")

elif page == "signup":
    st.title("Sign Up")

    with st.form("signup_form"):
        name     = st.text_input("Username")
        password = st.text_input("Password", type="password")
        confirm  = st.text_input("Confirm Password", type="password")

        submitted_signup = st.form_submit_button("Sign Up")
        submitted_login  = st.form_submit_button("Login Instead")

    if submitted_signup:
        name = name.strip()
        if not name or not password.strip() or not confirm:
            st.error("Please fill in all fields")
        elif password != confirm:
            st.error("Passwords do not match")
        else:
            if get_lockout(client_id) > time.time():
                st.error("You cannot create a new free account yet. Try again later.")
                st.stop()
            if add_user(name, 'F', password):
                st.session_state['signup_success'] = True
                st.session_state['auth_stat'] = None
                set_page("login")
            else:
                st.error("Username already exists")

    if submitted_login:
        st.session_state['auth_stat'] = None
        set_page("login")

elif page == "moderation":
    if st.session_state['type'] != 'S':
        st.error("Access denied.")
        set_page("main")
    else:
        st.title("🛠️ Moderation Panel")
        st.subheader("Pending Blacklist Submissions")

        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT word FROM blacklist WHERE status = 'pending'")
        pending_words = cur.fetchall()

        if not pending_words:
            st.info("No pending words.")
        else:
            for row in pending_words:
                word = row['word']
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"🔸 {word}")
                with col2:
                    if st.button("✅ Approve", key=f"approve_{word}"):
                        cur.execute(
                            "UPDATE blacklist SET status = 'approved' WHERE word = %s",
                            (word,)
                        )
                        con.commit()
                        st.rerun()
                    if st.button("❌ Reject", key=f"reject_{word}"):
                        cur.execute(
                            "DELETE FROM blacklist WHERE word = %s",
                            (word,)
                        )
                        con.commit()
                        st.rerun()

        st.subheader("Pending Paid User Requests")
        cur.execute("SELECT client_id, req_ts FROM upgrade")
        requests = cur.fetchall()

        if requests:
            for row in requests:
                name        = row['client_id']
                ts_int      = row['req_ts']
                ts_str      = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts_int))
                col1, col2  = st.columns([3, 1])
                with col1:
                    st.write(f"🔸 User: {name} (Requested at: {ts_str})")
                with col2:
                    if st.button("✅ Approve", key=f"approve_{name}"):
                        free_to_paid(name)
                        cur.execute(
                            "DELETE FROM upgrade WHERE client_id = %s",
                            (name,)
                        )
                        con.commit()
                        st.rerun()
                    if st.button("❌ Decline", key=f"decline_{name}"):
                        cur.execute(
                            "DELETE FROM upgrade WHERE client_id = %s",
                            (name,)
                        )
                        con.commit()
                        st.rerun()
        else:
            st.info("No pending requests.")
        
        st.subheader("Resolve Complaints")
        complaints = get_complaint_super()
        if not complaints:
            st.info("No pending complaints.")
        else:
            for complaint in complaints:
                ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(complaint['created_ts']))
                with st.expander(f"Complaint #{complaint['complaint_id']} on '{complaint['title']}' at {ts}"):
                    st.write(f"**Complainant**: {complaint['complainant']}")
                    st.write(f"**Complained**: {complaint['complained']}")
                    st.write(f"**Description**: {complaint['description']}")
                    if complaint['responses']:
                        st.write("**Responses**:")
                        for resp in complaint['responses']:
                            rts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(resp['created_ts']))
                            st.write(f"- {resp['client_id']} at {rts}: {resp['response']}")
                    else:
                        st.write("**Responses**: None")
                    with st.form(key=f"resolve_{complaint['complaint_id']}"):
                        complainant_penalty = st.number_input(
                            f"Penalty for {complaint['complainant']} (tokens)",
                            min_value=0, step=1, key=f"comp_penalty_{complaint['complaint_id']}"
                        )
                        complained_penalty = st.number_input(
                            f"Penalty for {complaint['complained']} (tokens)",
                            min_value=0, step=1, key=f"compld_penalty_{complaint['complaint_id']}"
                        )
                        submit = st.form_submit_button("Resolve")
                        if submit:
                            success, message = resolve_complaint(
                                complaint['complaint_id'], complainant_penalty, complained_penalty
                            )
                            if success:
                                st.success(message)
                                st.rerun()
                            else:
                                st.error(message)

        con.close()

        if st.button("◀️ Back to Main Page"):
            set_page("main")
        if st.button("Logout"):
            logout_user()

elif page == "logs":
    if st.session_state['type'] != 'S':
        st.error("Access denied.")
        set_page("main")
    else:
        st.title("📜 Censor Logs")

        con = get_connection()
        cur = con.cursor()
        cur.execute(
            "SELECT client_id AS user, original_word, event_ts "
            "FROM censor_log "
            "ORDER BY event_ts DESC"
        )
        logs = cur.fetchall()
        con.close()

        if not logs:
            st.info("No censored words recorded.")
        else:
            for row in logs:
                user    = row['user']
                word    = row['original_word']
                ts      = row['event_ts']
                ts_fmt  = time.strftime('%Y-%m-d %H:%M:%S', time.localtime(ts))
                st.write(f"🔸 `{word}` was submitted by **{user}** at `{ts_fmt}`")

        if st.button("◀️ Back to Main Page"):
            set_page("main")
        if st.button("Logout"):
            logout_user()

elif page == "history":
    if st.session_state['type'] != 'P':
        st.error("Access denied.")
        set_page("main")
    else:
        st.title("📜 Submission History")
        history = get_submission(st.session_state['client_id'])
        
        if not history:
            st.info("No submission recorded.")
        else:
            st.subheader("Past Submissions")
            for original, corrected, error, timestamp in history:
                ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                with st.expander(f"Submitted at {ts} ({'Error' if error else 'No Error'})"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("Original Text")
                        st.text_area("", original, height=150, disabled=True, key=f"original_{timestamp}")
                    with col2:
                        st.markdown("Corrected Text")
                        st.text_area("", corrected, height=150, disabled=True, key=f"corrected_{timestamp}")
        
        if st.button("◀️ Back to Main Page"):
            set_page("main")
        
        if st.button("Logout"):
            logout_user()

elif page == "collab":
    if not st.session_state['complaints_checked']:
        handle_complaint()
    
    # boost the max-width of the main content only on this page
    st.markdown("""<style> .block-container {max-width: 1600px;} </style>""", unsafe_allow_html=True)

    st.header("🤝 Collaborative Editor")

    file_id = st.session_state.get("current_file")
    if file_id is None:
        st.error("No document selected. Go back to 📁 My Documents.")
        if st.button("◀️ Back to Main"):
            st.session_state.pop("current_file", None)
            set_page("main")
        st.stop()

    orig_key      = f"collab_orig_{file_id}"
    rendered_key  = f"collab_html_{file_id}"
    clean_key     = f"collab_clean_{file_id}"

    orig = st.session_state.get(orig_key)
    if orig is None:
        orig = get_file_content(file_id)

    if st.session_state.pop("collab_saved", False):
        st.success("💾 Agreed text saved. All collaborators now see this version.")

    left, right = st.columns(2)

    with left:
        st.subheader("🖋️ Original")
        new_orig = st.text_area(
            label="", value=orig,
            height=400, key=f"collab_edit_{file_id}",
            label_visibility="collapsed",
            placeholder=""
        )
        if st.button("🔄 Submit for correction"):
            st.session_state[orig_key] = new_orig
            correct_text(new_orig)
            st.session_state[rendered_key] = st.session_state["rendered_html"]
            st.session_state[clean_key]    = st.session_state["corrected_text"]
            st.rerun()

    with right:
        st.subheader("✅ Agreed-Upon Text")
        html_blob = st.session_state.get(rendered_key, "")
        wrapped   = wrap_scrollable(html_blob, max_height=400)
        edited    = html_viewer(html=wrapped, height=400)
        if edited is not None:
            st.session_state[clean_key] = edited

        if st.button("💾 Save Agreed Text"):
            agreed = st.session_state.get(clean_key)
            if agreed:
                clean = html_to_clean_text(agreed)
                update_file_content(file_id, clean)
                # next load of this page (any collaborator) starts from the saved version
                st.session_state[orig_key] = clean
                st.session_state.pop(rendered_key, None)
                st.session_state.pop(clean_key, None)
                st.session_state.pop(f"collab_edit_{file_id}", None)
                st.session_state["collab_saved"] = True
                st.rerun()
            else:
                st.warning("Nothing to save yet — submit the text for correction first.")

    if st.session_state['type'] == 'P':
        st.markdown("---")
        st.subheader("File a Complaint")
        with st.form("complaint_form"):
            complained_user = st.text_input("Collaborator Username")
            description = st.text_area("Complaint Description")
            submit_complaint_btn = st.form_submit_button("Submit Complaint")
            if submit_complaint_btn:
                if not complained_user or not description:
                    st.error("Please fill in all fields.")
                elif submit_complaint(file_id, st.session_state['name'], complained_user, description):
                    st.success("Complaint submitted for super-user review.")
                else:
                    st.error("Invalid collaborator or complaint already exists.")

    if st.button("◀️ Back to Main"):
        set_page("main")

elif page == "main":
    if not st.session_state['complaints_checked']:
        handle_complaint()

    if not st.session_state['auth_stat']:
        set_page("login")
    else:
        if st.session_state['type'] == 'P':
            # Let the sidebar shrink below Streamlit's default minimum; once it
            # gets narrow, hide the widget labels and keep only their icons.
            st.markdown("""
                <style>
                  section[data-testid="stSidebar"] {
                      min-width: 130px !important;
                      container-type: inline-size;
                  }
                  [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"] p {
                      white-space: nowrap;
                      overflow: hidden;
                      text-overflow: ellipsis;
                  }
                  /* hide labels before they start wrapping */
                  @container (max-width: 260px) {
                    .st-key-sidebar_logout [data-testid="stMarkdownContainer"],
                    [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"] {
                        display: none;
                    }
                  }
                </style>
            """, unsafe_allow_html=True)
            with st.sidebar:
                if st.button("Logout", icon="🚪", key="sidebar_logout"):
                    logout_user()
            # show invites only to paid users
            with st.sidebar.expander("Notifications", icon="🔔", expanded=False):
                invites = list_invites_for(st.session_state['name'])
                if not invites:
                    st.write("No new invites.")
                else:
                    for inv in invites:
                        ts = time.strftime(
                            "%Y-%m-%d %H:%M",
                            time.localtime(inv["requested_ts"])
                        )
                        st.write(f"📩 **{inv['title']}** from _{inv['inviter']}_ at {ts}")
                        col1, col2 = st.columns([1,1])
                        with col1:
                            if st.button("Accept", key=f"accept_{inv['invite_id']}"):
                                respond_invite(inv['invite_id'], True)
                                st.rerun()
                        with col2:
                            if st.button("Reject", key=f"reject_{inv['invite_id']}"):
                                respond_invite(inv['invite_id'], False)
                                st.rerun()
        
            with st.sidebar.expander("Shared Documents", icon="📁", expanded=False):
                files = list_files_for(st.session_state['name'])
                if not files:
                    st.write("No documents yet.")
                else:
                    for f in files:
                        ts = time.strftime("%Y-%m-%d", time.localtime(f["created_ts"]))
                        if st.button(f"{f['title']}  ({ts})", key=f"file_{f['file_id']}"):
                            st.session_state["current_file"] = f["file_id"]
                            set_page("collab")

        st.title("📝 LLM-Based Text Editor")
        st.write(f"## Hello, {st.session_state['name']}!")

        #---Super-User Buttons---#
        if st.session_state['type'] == 'S':
            st.markdown("### 🔧 Access Super-User Controls Below")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Go to Moderation Panel"):
                    set_page("moderation")
            with c2:
                if st.button("View Logs"):
                    set_page("logs")
            st.markdown("")
            st.markdown("")
            st.markdown("")
            st.markdown("")

        if st.session_state['type'] == 'P':
            with st.expander("View/Add Tokens", expanded = True):
                st.caption("ℹ️ For demo purposes, paid users can simply add tokens to their account for free, as per the requirements of the original assignment.")
                show_paid_user_metrics(st.session_state['client_id'])
                token_input = st.number_input("Enter Tokens", min_value=1, step=1)
                
                if st.button("Add Tokens"):
                    update_token(st.session_state['client_id'], token_input, 0)
                    st.rerun()
        
        elif st.session_state['type'] == 'F':
            lock_time = get_lockout(client_id)
            if lock_time and lock_time < time.time():
                remove_lockout(client_id)
            
            if st.button("Sign up as Paid User"):
                if request_free_to_paid(st.session_state['name']):
                    st.success("Request submitted. Awaiting super user approval.")
                else:
                    st.info("You have already submitted a request.")
            
            if st.button("Test as Super User"):
                free_to_super(st.session_state['name'])
                st.session_state['type'] = 'S'
                st.rerun()

        file_text = ""
        typed_input = ""

        if "uploaded_file" not in st.session_state:
            st.session_state['uploaded_file'] = None

        if st.session_state['type'] != 'S':
            st.markdown("### 📤 Upload a `.txt` file")
            uploaded = st.file_uploader("Choose a text file", type=["txt"])
            if uploaded:
                st.session_state['uploaded_file'] = uploaded
                file_text = uploaded.read().decode("utf-8")
            elif st.session_state['uploaded_file'] is not None and uploaded is None:
                st.session_state['uploaded_file'] = None

            if st.session_state['uploaded_file'] is None:
                st.markdown("### 💻 Or input your text:")
                typed_input = st.text_area(label="Your text:", placeholder="Start typing here...", height=170)

            user_input = (typed_input or file_text).rstrip()

            word_count = len(user_input.split())
            available = None
            if st.session_state['type'] == 'P':
                available, _ = get_token(st.session_state['client_id'])

            if st.session_state['type'] == 'F' and word_count > 20:
                counter_html = f"<span style='color:red;'>Word count: {word_count} (Limit: 20. Submitting will result in a 3 minute timeout.)</span>"
            elif available is not None and word_count > available:
                counter_html = f"<span style='color:red;'>Word count: {word_count} (Exceeds available tokens: {available}. Submitting will cut your tokens in half.)</span>"
            else:
                counter_html = f"Word count: {word_count}"
            st.markdown(f"<div id='live-word-count'>{counter_html}</div>", unsafe_allow_html=True)

            # Live-update the counter on each keystroke, client-side only.
            # The script never modifies Streamlit's (React-managed) nodes: it keeps its
            # own overlay element and only toggles the server counter's visibility.
            # Mutating React's DOM (e.g. innerHTML on #live-word-count) crashes React
            # with removeChild errors on the next rerun.
            if st.session_state['uploaded_file'] is None:
                components.html(f"""
                    <script>
                    const USER_TYPE = {json.dumps(st.session_state['type'])};
                    const AVAILABLE = {json.dumps(available)};
                    const doc = window.parent.document;

                    function markup(n) {{
                        if (USER_TYPE === 'F' && n > 20) {{
                            return "<span style='color:red;'>Word count: " + n +
                                " (Limit: 20. Submitting will result in a 3 minute timeout.)</span>";
                        }}
                        if (AVAILABLE !== null && n > AVAILABLE) {{
                            return "<span style='color:red;'>Word count: " + n +
                                " (Exceeds available tokens: " + AVAILABLE +
                                ". Submitting will cut your tokens in half.)</span>";
                        }}
                        return "Word count: " + n;
                    }}

                    // Keep our own element next to the server-rendered counter; hide the
                    // server one. Re-runs safely after every Streamlit rerender.
                    function sync(copyFromServer) {{
                        const counter = doc.getElementById('live-word-count');
                        if (!counter) return null;
                        let live = doc.getElementById('live-word-count-live');
                        if (!live || !live.isConnected || live.previousElementSibling !== counter) {{
                            if (live) live.remove();
                            live = doc.createElement('div');
                            live.id = 'live-word-count-live';
                            counter.after(live);
                        }}
                        if (counter.style.display !== 'none') counter.style.display = 'none';
                        if (copyFromServer && live.dataset.src !== counter.textContent) {{
                            live.innerHTML = counter.innerHTML;
                            live.dataset.src = counter.textContent;
                        }}
                        return live;
                    }}

                    // Delegated listener survives Streamlit replacing the textarea node.
                    doc.addEventListener('input', (e) => {{
                        const t = e.target;
                        if (!t || t.tagName !== 'TEXTAREA' ||
                            t.getAttribute('placeholder') !== 'Start typing here...') return;
                        const live = sync(false);
                        if (!live) return;
                        const text = t.value.trim();
                        const n = text ? text.split(/\\s+/).length : 0;
                        live.innerHTML = markup(n);
                        live.dataset.src = live.textContent;
                    }}, true);

                    // After a rerun the server counter is the source of truth again.
                    new MutationObserver(() => sync(true)).observe(
                        doc.body, {{ childList: true, subtree: true }}
                    );
                    sync(true);
                    </script>
                """, height=1)  # height 0 keeps the iframe unmounted (lazy-loading), so the script never runs

            correction_type = "LLM Correction"
            if st.session_state['type'] == 'P':
                correction_type = st.radio(
                    "Select Correction Type",
                    ["LLM Correction", "Self Correction"],
                    key="correction_type"
                )
            
            # ─── Submit button ───
            if st.button("Submit"):
                if user_input.strip():
                    word_count = len(user_input.split())
                    
                    instruction_like = is_instruction_like(user_input)

                    if instruction_like:
                        st.warning("⚠️ Your input looks like an instruction. If you're trying to correct a real sentence, rephrase it.")

                    else:
                        # Free user flow
                        if st.session_state['type'] == 'F':
                            if word_count > 20:
                                set_lockout(client_id, 180)
                                logout_user()
                            else:
                                st.session_state["user_input"] = user_input
                                correct_text(user_input)
                        # Paid user flow: defer to confirmation
                        elif st.session_state['type'] == 'P':
                            available, used = get_token(st.session_state['client_id'])
                            if available >= word_count:
                                # flag for confirmation on next rerun
                                st.session_state["pending_submit"] = True
                                st.session_state["pending_input"]  = user_input
                                st.session_state["pending_count"]  = word_count
                                st.session_state["pending_correction_type"] = correction_type
                                st.session_state["original_input"] = user_input
                            else:
                                penalty = available // 2
                                update_token(st.session_state['client_id'], -penalty, penalty)
                                new_available, _ = get_token(st.session_state['client_id'])
                                st.warning(
                                    f"⚠️ Not enough tokens. "
                                    f"Half your tokens were deducted. Remaining: {new_available}"
                                )
                                st.stop()
                else:
                    st.warning("Input can't be empty.")

            if st.session_state.get("pending_self_correction"):
                st.subheader("Self-Correction")
                # Display highlighted incorrect words
                if st.session_state.get("rendered_html"):
                    st.markdown("### Text with Incorrect Words Highlighted")
                    raw = st.session_state["rendered_html"]
                    wrapped = wrap_scrollable(raw, max_height=255)
                    st.markdown("Words in green contain grammatical errors. Edit them below.")
                    html_viewer(html=wrapped, height=255)
                
                self_corrected = st.text_area(
                    "Edit your text below:",
                    value=st.session_state["self_corrected_text"],
                    height=200,
                    key="self_corrected_area"
                )
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Confirm Self-Correction", key="confirm_self_correction"):
                        if not st.session_state["user_input"]:
                            st.error("Original input is missing. Please submit text again.")
                            st.session_state["pending_self_correction"] = False
                            st.session_state["self_corrected_text"] = None
                            st.rerun()
                        elif self_corrected.strip():
                            tokens = self_correct_cost(st.session_state["user_input"], self_corrected)
                            available, _ = get_token(st.session_state['client_id'])
                            if available >= tokens:
                                update_token(st.session_state['client_id'], -tokens, tokens)
                                set_submission(
                                    st.session_state['client_id'],
                                    st.session_state["user_input"],
                                    self_corrected,
                                    1 if st.session_state["user_input"] != self_corrected else 0
                                )
                                st.session_state["corrected_text"] = self_corrected
                                st.session_state["can_download"] = True
                                st.session_state["pending_self_correction"] = False
                                st.session_state["self_corrected_text"] = None
                                st.session_state["tokens"] = tokens
                                st.session_state["rendered_html"] = None  # Clear highlighted text
                                st.success(f"💰 Deducted {tokens} tokens for self-correction.")
                                st.rerun()
                            else:
                                st.error(f"Not enough tokens. Required: {tokens}, Available: {available}")
                        else:
                            st.warning("Corrected text can't be empty.")
                with col2:
                    if st.button("Cancel", key="cancel_self_correction"):
                        st.session_state["pending_self_correction"] = False
                        st.session_state["self_corrected_text"] = None
                        st.session_state["rendered_html"] = None  # Clear highlighted text
                        st.rerun()
            
            # ─── Confirmation UI for paid users ───
            if st.session_state.get("pending_submit"):
                tokens = st.session_state["pending_count"]
                correction_type = st.session_state["pending_correction_type"]
                if correction_type == "LLM Correction":
                    st.warning(f"⚠️ {tokens} tokens will be deducted for LLM correction. Are you sure?")
                else:
                    estimated_tokens = self_correct_cost(st.session_state["pending_input"], st.session_state["pending_input"])
                    st.warning(f"⚠️ Tokens will be deducted based on actual self-correction edits. Are you sure?")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Yes, submit", key="confirm_submit"):
                        # perform the correction
                        if correction_type == "LLM Correction":
                            st.session_state["original_input"] = st.session_state["pending_input"]  # Store original input
                            correct_text(st.session_state["pending_input"])
                        else:
                            st.session_state["self_corrected_text"] = st.session_state["pending_input"]
                            st.session_state["user_input"] = st.session_state["pending_input"]
                            correct_text(st.session_state["pending_input"], self_correction=True)  # Highlight errors
                            st.session_state["pending_self_correction"] = True
                        # clear the pending flags
                        for k in ("pending_submit", "pending_count", "pending_correction_type", "pending_input"):
                            st.session_state.pop(k, None)
                        st.rerun()
                with col2:
                    if st.button("Cancel", key="cancel_submit"):
                        # abort
                        for k in ("pending_submit", "pending_input", "pending_count", "pending_correction_type"):
                            st.session_state.pop(k, None)

            if st.session_state['type'] == 'P':
                if st.button("View Submission History", key="view_history"):
                    set_page("history")

            if st.session_state.get("downloaded_success"):
                st.success(st.session_state["downloaded_success"])
                del st.session_state["downloaded_success"]

            if st.session_state.get("rendered_html") and not st.session_state.get("can_download") and not st.session_state.get("pending_self_correction"):
                st.subheader("✅ Corrected Text")
                raw = st.session_state["rendered_html"]
                wrapped = wrap_scrollable(raw, max_height=255)
                edited  = html_viewer(html=wrapped, height=255)
                if edited is not None:
                    st.session_state["corrected_text"] = edited
                if st.session_state['type'] == 'F':
                    st.info("💡 Sign up as a paid user to download your corrected text, invite collaborators, and unlock more features.")

            if st.session_state['type'] == 'P':
                if st.session_state.get("corrected_text") and not st.session_state.get("can_download"):

                    if not st.session_state.get("confirming_purchase"):
                        if st.button("🔒 Confirm Edits"):
                            st.session_state["confirming_purchase"] = True
                            st.rerun()
                        st.markdown("⚠️ Pressing this will lock further edits.", unsafe_allow_html=True)

                    else:
                        # clean up the HTML into plain text
                        clean_text = html_to_clean_text(st.session_state["corrected_text"])

                        # figure out the original text to compare against
                        orig = st.session_state.get("original_input") or st.session_state.get("pending_input")
                        if not orig:
                            st.error("Original input is missing. Please resubmit your text.")
                            st.session_state["confirming_purchase"] = False
                            st.rerun()
                            st.stop()

                        # count how many tokens this change will cost
                        tokens = count_price(orig, clean_text)
                        available, _ = get_token(st.session_state['client_id'])
                        if available < tokens:
                            st.error(f"Not enough tokens to confirm edits. Required: {tokens}, Available: {available}")
                            st.session_state["confirming_purchase"] = False
                            st.rerun()
                            st.stop()

                        st.warning(f"⚠️ This will cost you {tokens} tokens. Proceed?")

                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("✅ Yes", key="confirm_yes"):
                                try:
                                    update_token(
                                        st.session_state['client_id'],
                                        -tokens,  # subtract from available
                                        tokens    # add to used
                                    )
                                    # now allow download and reset for a fresh file next time
                                    st.session_state["can_download"]        = True
                                    st.session_state["confirming_purchase"] = False
                                    st.session_state["tokens"]              = tokens
                                    st.session_state.pop("current_file", None)
                                    st.rerun()
                                except ValueError as e:
                                    st.error(str(e))
                                    st.session_state["confirming_purchase"] = False
                                    st.rerun()
                        with c2:
                            if st.button("❌ No", key="confirm_no"):
                                st.session_state["confirming_purchase"] = False
                                st.info("Edit confirmation cancelled.")
                                st.rerun()

                if st.session_state.get("can_download"):
                    st.markdown("### 📄 Preview of Approved Edits")
                    clean_text = html_to_clean_text(st.session_state["corrected_text"])

                    st.text_area("", clean_text, height=200, disabled=True)
                    st.success(f"💰 Deducted {st.session_state['tokens']} tokens for confirmed edits.")

                    file_id = st.session_state.get("current_file")
                    if file_id is None:
                        title = st.text_input("Document title", value="Untitled", key="doc_title")
                        if st.button("Initialize File"):
                            fid = create_file(
                                owner=st.session_state["name"],
                                title=title
                            )
                            # immediately write the confirmed text into the DB
                            clean = html_to_clean_text(st.session_state["corrected_text"])
                            update_file_content(fid, clean)
                            st.session_state["current_file"] = fid
                            st.rerun()
                        st.stop()  # wait for the user to initialize
                    else:
                        st.success(f"Editing Document #{file_id}")

                    # (B) Invite UI: ALWAYS render the input, then the send button
                    invitee = st.text_input("Invite collaborator (username):", key="invitee_input")
                    if st.button("📨 Send Invite"):
                        ok = invite_user(
                            file_id=file_id,
                            inviter=st.session_state["name"],
                            invitee=invitee.strip()
                        )
                        if ok:
                            st.success("✅ Invite sent!")
                        else:
                            st.error("❌ User not found or already invited.")

                    if st.download_button(
                        label="📥 Download .txt File (5 Tokens)",
                        data=clean_text,
                        file_name="corrected_text.txt",
                        mime="text/plain",
                    ):
                        available, _ = get_token(st.session_state['client_id'])
                        if available >= 5:
                            update_token(st.session_state['client_id'], -5, 5)
                            st.session_state["downloaded_success"] = f"File downloaded. 5 tokens deducted. Remaining: {available - 5}"
                            st.session_state["can_download"] = False
                            st.session_state["rendered_html"] = None
                            st.session_state["corrected_text"] = None
                            st.session_state["original_input"] = None  # Clear original input
                            st.rerun()
                        else:
                            st.error("Not enough tokens to download the file.")

            render_blacklist_form()

        if st.button("Logout"):
            logout_user()