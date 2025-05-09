import streamlit as st
import requests
import os

# --- Streamlit Page Configuration ---
st.set_page_config(page_title="GitHub to Markdown", layout="wide")

# --- Configuration ---
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000/api/fetch-repo")

# --- Helper function to display file tree (manual expand/collapse) ---
def display_tree_manual_expand(node, on_file_click, level=0, parent_key_prefix="root"):
    file_indent = "    " * level
    node_unique_id_part = node.get('path', f"{node.get('name', 'unknown')}_{level}_{parent_key_prefix}")
    node_key_sanitized = node_unique_id_part.replace("/", "_").replace(".", "_").replace(" ", "_")

    if node.get('type') == 'dir':
        expanded_state_key = f"expanded_{node_key_sanitized}"
        if expanded_state_key not in st.session_state:
            st.session_state[expanded_state_key] = (level < 1) # Expand first level by default

        # Use a container for the directory entry
        # dir_entry_container = st.container() # Optional: if more complex layout needed per item
        # with dir_entry_container:
        # Create clickable area for directory toggle
        cols = st.columns([1, 10]) # Simple ratio for indent effect + label
        with cols[0]: # Indentation column
            # Use Markdown for more control over spacing if needed, or just empty for structure
            st.markdown(f"<div style='width: {level*20}px;'></div>", unsafe_allow_html=True)
        
        with cols[1]: # Label and toggle button column
            icon = "▼" if st.session_state[expanded_state_key] else "▶"
            dir_label = f"{icon} 📁 {node['name']}"
            button_toggle_key = f"toggle_btn_{node_key_sanitized}"
            if st.button(dir_label, key=button_toggle_key, help=f"Click to expand/collapse {node['name']}", use_container_width=True):
                st.session_state[expanded_state_key] = not st.session_state[expanded_state_key]
                # No explicit rerun needed, button click triggers it

        if st.session_state[expanded_state_key]:
            children = node.get('children', [])
            if not isinstance(children, list): children = []
            
            # Children are rendered directly, relying on Streamlit's flow.
            # An inner container for children can help visually group them if desired.
            # with st.container(): 
            #    st.markdown(f"<div style='margin-left: 25px;'>", unsafe_allow_html=True) # Indent children block
            for child_node in children:
                display_tree_manual_expand(child_node, on_file_click, level + 1, parent_key_prefix=node_key_sanitized)
            #    if children: st.markdown("</div>", unsafe_allow_html=True)


    elif node.get('type') == 'file':
        file_button_key = f"file_btn_{node_key_sanitized}"
        
        file_cols = st.columns([1, 10]) # Similar column structure for files
        with file_cols[0]: # Indentation column
            st.markdown(f"<div style='width: {(level*20)+10}px;'></div>", unsafe_allow_html=True) # Files indented a bit more
        
        with file_cols[1]: # File button column
            if st.button(f"📄 {node['name']}", key=file_button_key, help=f"View content of {node['path']}", use_container_width=True):
                on_file_click(node['path'])

# --- Initialize session state variables ---
default_session_states = {
    'repo_data': None,
    'selected_file_path': None,
    'error_message': None,
    'loading': False,
    'user_github_token': "",
    'last_repo_url': "" # Initialize last_repo_url as empty
}
for key, default_value in default_session_states.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

# --- Main App UI ---
st.title("GitHub Repository to Markdown 📝")
st.markdown("""
Enter a public GitHub repository URL. Optionally, provide a GitHub Personal Access Token (PAT)
for private repositories or to increase API rate limits.
The token is sent to the backend for API requests and is not stored long-term by this UI.
""")

# --- Inputs: Repo URL and Optional GitHub Token ---
repo_url_input = st.text_input(
    "GitHub Repository URL:",
    value=st.session_state.last_repo_url, # Use the session state value (which starts empty)
    placeholder="https://github.com/owner/repo", # This placeholder guides the user
    key="repo_url_input_key"
)
# Store the current input in session state so it persists across reruns if user types something
st.session_state.last_repo_url = repo_url_input

github_token_input = st.text_input(
    "Optional GitHub Token (PAT):",
    type="password",
    value=st.session_state.user_github_token,
    help="Your GitHub Personal Access Token. Used for private repos or higher rate limits.",
    key="github_token_input_key"
)
st.session_state.user_github_token = github_token_input


# --- Top Buttons (Fetch & Download) ---
top_button_cols = st.columns([1, 1, 3]) # Fetch Button, Download Button, Spacer

with top_button_cols[0]: # Fetch Button
    if st.button("🚀 Fetch Repository", type="primary", disabled=st.session_state.loading, use_container_width=True, key="fetch_button"):
        if not repo_url_input or not repo_url_input.startswith("https://github.com/"):
            st.session_state.error_message = "Please enter a valid GitHub repository URL."
            st.session_state.repo_data = None
            st.session_state.selected_file_path = None
        else:
            st.session_state.loading = True
            st.session_state.error_message = None
            st.session_state.repo_data = None
            st.session_state.selected_file_path = None
            
            # Prepare payload for backend, including the optional token
            payload = {
                "github_url": repo_url_input,
                "github_token": st.session_state.user_github_token if st.session_state.user_github_token else None
            }

            with st.spinner("Fetching repository data..."):
                try:
                    response = requests.post(BACKEND_API_URL, json=payload, timeout=90)
                    response.raise_for_status()
                    st.session_state.repo_data = response.json()
                except requests.exceptions.HTTPError as http_err:
                    error_detail = str(http_err)
                    try: error_detail = http_err.response.json().get("detail", str(http_err))
                    except: pass
                    st.session_state.error_message = f"Error from backend: {error_detail}"
                    st.session_state.repo_data = None
                except requests.exceptions.RequestException as req_err:
                    st.session_state.error_message = f"Network error: {req_err}"
                    st.session_state.repo_data = None
                except Exception as e:
                    st.session_state.error_message = f"Unexpected error: {e}"
                    st.session_state.repo_data = None
                finally:
                    st.session_state.loading = False

with top_button_cols[1]: # Download Button
    if st.session_state.repo_data and not st.session_state.error_message:
        all_markdown_content = st.session_state.repo_data.get('all_files_markdown', "")
        if all_markdown_content:
            repo_name_for_download = "repository"
            if repo_url_input:
                try:
                    repo_name_match = repo_url_input.rstrip('/').split('/')[-1]
                    if repo_name_match: repo_name_for_download = repo_name_match.replace(".git", "")
                except: pass
            st.download_button(
                label="⬇️ Download Markdown",
                data=all_markdown_content,
                file_name=f"{repo_name_for_download}_contents.md",
                mime="text/markdown",
                disabled=st.session_state.loading,
                use_container_width=True,
                key="download_markdown_button"
            )

if st.session_state.error_message:
    st.error(st.session_state.error_message)

st.divider()

if st.session_state.repo_data and not st.session_state.error_message:
    data = st.session_state.repo_data
    repo_tree_data = data.get('tree')
    file_contents_map = data.get('file_contents', {})

    st.subheader(f"Repository Structure: {repo_tree_data.get('name', 'N/A') if repo_tree_data else 'N/A'}")
    col_tree, col_content = st.columns([2, 3]) # Give tree a bit more space

    with col_tree:
        st.markdown("#### File Tree")
        if repo_tree_data:
            def handle_file_click(path_of_file_clicked):
                st.session_state.selected_file_path = path_of_file_clicked
            display_tree_manual_expand(repo_tree_data, handle_file_click)
        else:
            st.info("Repo structure appears here.")

    with col_content:
        st.markdown("#### File Content")
        if st.session_state.selected_file_path:
            st.text(f"Displaying: {st.session_state.selected_file_path}")
            content_to_display = file_contents_map.get(st.session_state.selected_file_path, "Content not found.")
            language_for_code = None
            if '.' in st.session_state.selected_file_path:
                extension = st.session_state.selected_file_path.split('.')[-1].lower()
                common_langs = {"py": "python", "js": "javascript", "html": "html", "css": "css", "md": "markdown", "json": "json", "yaml": "yaml", "yml": "yaml", "sh": "bash", "txt": "text"}
                language_for_code = common_langs.get(extension)
            st.code(content_to_display, language=language_for_code, line_numbers=True)
        else:
            st.info("Select a file to view content.")

st.sidebar.header("About")
st.sidebar.info(
    "GitHub repo to Markdown converter. Fetches repo structure and file contents. "
    "Optionally use a GitHub PAT for private repos or higher rate limits."
)