import os
import base64
import re
from typing import List, Dict, Any, Optional

import httpx # For making asynchronous HTTP requests to GitHub API
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv # For local development with .env file

# Assuming models.py is in the same 'app' directory
from .models import RepoRequest, FileNode, RepoDataResponse

load_dotenv() # Load .env file if present (for local development)

app = FastAPI(
    title="GitHub to Markdown Service",
    description="Fetches GitHub repository contents and compiles them into Markdown.",
    version="1.0.0"
)

# --- CORS Configuration ---
# Read allowed origins from environment variable, fallback for local dev
ALLOWED_ORIGINS_STRING = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501")
allowed_origins_list: List[str] = [origin.strip() for origin in ALLOWED_ORIGINS_STRING.split(',') if origin.strip()]
if not allowed_origins_list: # If string was empty or only whitespace after splitting
    allowed_origins_list = ["*"] # Fallback to allow all if misconfigured, or choose a more restrictive default in prod

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods (GET, POST, etc.)
    allow_headers=["*"], # Allows all headers
)

# --- GitHub API Configuration ---
GITHUB_API_BASE_URL = "https://api.github.com"
# Fallback token from backend environment if user doesn't provide one via Streamlit
BACKEND_GITHUB_TOKEN_ENV = os.getenv("GITHUB_TOKEN")

# --- List of common binary/image file extensions to ignore content for ---
BINARY_FILE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg', '.ico',
    '.mp3', '.wav', '.aac', '.ogg', '.flac', '.mp4', '.mov', '.avi', '.mkv', '.webm',
    '.zip', '.tar', '.gz', '.rar', '.7z', '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx',
    '.exe', '.dll', '.so', '.dylib', '.jar', '.pyc', '.pyo', '.class', '.o', '.a', '.obj',
    '.eot', '.otf', '.ttf', '.woff', '.woff2', '.DS_Store'
}

def is_binary_file(file_path: str) -> bool:
    """Checks if a file is likely binary based on its extension."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in BINARY_FILE_EXTENSIONS

async def get_github_api_headers(user_provided_token: Optional[str] = None) -> dict:
    """Constructs headers for GitHub API calls, prioritizing user-provided token."""
    headers = {"Accept": "application/vnd.github.v3+json", "X-GitHub-Api-Version": "2022-11-28"}
    token_to_use = user_provided_token or BACKEND_GITHUB_TOKEN_ENV
    if token_to_use:
        headers["Authorization"] = f"Bearer {token_to_use}" # Use Bearer for PATs
    return headers

def parse_github_url(url: str) -> tuple[str, str]:
    """Parses GitHub URL to extract owner and repository name."""
    match = re.match(r"https://github\.com/([^/]+)/([^/.]+?)(?:\.git)?(?:/tree/[^/]+(?:/.*)?)?/?$", url)
    if not match:
        raise ValueError("Invalid GitHub URL format. Expected: https://github.com/owner/repo")
    owner, repo = match.groups()
    return owner, repo

def generate_text_tree(node: FileNode, tree_lines: List[str], prefix: str = "", is_last_sibling: bool = True):
    """Recursively generates a text-based tree structure."""
    connector = "└── " if is_last_sibling else "├── "
    tree_lines.append(f"{prefix}{connector}{node.name}{'/' if node.type == 'dir' else ''}")
    
    if node.type == 'dir' and node.children:
        # Determine the prefix for children based on whether the current node is the last sibling
        child_prefix = prefix + ("    " if is_last_sibling else "│   ")
        num_children = len(node.children)
        for i, child in enumerate(node.children):
            generate_text_tree(child, tree_lines, child_prefix, i == (num_children - 1))

async def fetch_repo_contents_recursive(
    owner: str, repo: str, path: str = "",
    client: httpx.AsyncClient = None, user_provided_token: Optional[str] = None
) -> tuple[FileNode, List[Dict[str, str]]]: # Return FileNode for tree, and flat list of {path:content}
    
    # Headers for this specific call, considering the user_provided_token
    current_api_headers = await get_github_api_headers(user_provided_token)

    # Create a new client if one is not passed (for the initial call)
    async_client_manager = client if client else httpx.AsyncClient()
    
    try:
        if not client: # If we created the client, we should close it
            client_to_use = async_client_manager
        else: # If client was passed, use it
            client_to_use = client

        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        
        try:
            response = await client_to_use.get(url, headers=current_api_headers)
            response.raise_for_status() # Raises HTTPStatusError for 4xx/5xx responses
        except httpx.HTTPStatusError as e:
            detail_message = f"GitHub API error for {url}: Status {e.response.status_code}."
            if e.response.status_code == 404:
                detail_message = f"Repository or path not found: {owner}/{repo}/{path}"
            elif e.response.status_code == 403:
                # More detailed 403 message construction
                gh_response_json = {}
                try: gh_response_json = e.response.json()
                except: pass # Ignore if response is not JSON
                
                gh_message = gh_response_json.get("message", "Access forbidden by GitHub.")
                docs_url = gh_response_json.get("documentation_url", "")

                rate_limit_remaining = e.response.headers.get("X-RateLimit-Remaining")
                token_in_use = user_provided_token or BACKEND_GITHUB_TOKEN_ENV

                if rate_limit_remaining == "0":
                    detail_message = f"GitHub API rate limit exceeded. {gh_message}"
                elif not token_in_use:
                    detail_message = f"{gh_message} This could be a private repository (requires a GitHub token) or a rate limit issue. Docs: {docs_url}"
                else: # Token provided, but still 403
                    detail_message = f"{gh_message} Check token permissions or if it's a rate limit issue. Docs: {docs_url}"
            elif e.response.status_code == 401: # GitHub uses 401 for bad credentials
                 detail_message = "GitHub API: Bad credentials. Ensure your token is correct and has not expired."

            raise HTTPException(status_code=e.response.status_code, detail=detail_message)
        except httpx.RequestError as e: # For network errors, timeouts etc.
            raise HTTPException(status_code=503, detail=f"Network error while contacting GitHub API: {str(e)}")

        items_data = response.json()
        
        # Handle case where the path points directly to a single file
        if isinstance(items_data, dict) and items_data.get('type') == 'file':
            item = items_data
            item_path = item['path']
            file_content_str = f"[Content of binary/image file: {item_path}]" # Default placeholder

            if not is_binary_file(item_path):
                try:
                    if item.get('download_url'):
                        content_response = await client_to_use.get(item['download_url'], headers=current_api_headers)
                        content_response.raise_for_status()
                        file_content_str = content_response.content.decode('utf-8', errors='replace')
                    # No fallback to 'content' + 'base64' here, download_url is preferred for raw file content
                except Exception as e_content:
                    file_content_str = f"[Error fetching/decoding content for {item_path}: {str(e_content)}]"
            
            single_file_node = FileNode(name=item['name'], path=item_path, type='file', content=file_content_str)
            return single_file_node, [{"path": item_path, "content": file_content_str}]

        if not isinstance(items_data, list): # Should be a list for a directory
             raise HTTPException(status_code=500, detail=f"Unexpected response format from GitHub API for directory path: {path}. Expected a list.")

        # Process directory
        dir_name_for_node = path.split('/')[-1] if path else repo # Use repo name for the root node if path is empty
        current_node = FileNode(name=dir_name_for_node, path=path, type='dir', children=[])
        all_files_data_flat = [] # To store {path: 'path_str', content: 'content_str'}

        for item in items_data:
            item_path = item['path']
            if item['type'] == 'file':
                file_content_str = f"[Content of binary/image file: {item_path}]" # Default for binary files

                if not is_binary_file(item_path): # Only attempt to fetch content for non-binary files
                    try:
                        if item.get('download_url'):
                            content_response = await client_to_use.get(item['download_url'], headers=current_api_headers)
                            content_response.raise_for_status()
                            file_content_str = content_response.content.decode('utf-8', errors='replace')
                        # No 'content' + 'base64' fallback here, assume download_url is primary for raw content
                        else: # Should not happen if download_url is standard
                            file_content_str = f"[Text content not fetched for {item_path} - no download URL]"
                    except Exception as e_file:
                         file_content_str = f"[Error fetching/decoding text content for {item_path}: {str(e_file)}]"
                
                current_node.children.append(FileNode(name=item['name'], path=item_path, type='file', content=file_content_str))
                all_files_data_flat.append({"path": item_path, "content": file_content_str})

            elif item['type'] == 'dir':
                # Recursively fetch contents of the subdirectory
                # Pass the client and user_provided_token down
                subtree_node, subtree_files_data = await fetch_repo_contents_recursive(
                    owner, repo, item_path, client_to_use, user_provided_token
                )
                current_node.children.append(subtree_node)
                all_files_data_flat.extend(subtree_files_data)
                
        if current_node.children: # Sort children: directories first, then files, alphabetically
            current_node.children.sort(key=lambda x: (x.type != 'dir', x.name.lower()))

        return current_node, all_files_data_flat
    finally:
        if not client: # If we created the client in this call, close it
            await async_client_manager.aclose()


@app.post("/api/fetch-repo", response_model=RepoDataResponse, summary="Fetch Repository Contents")
async def fetch_repo_api(request: RepoRequest):
    """
    Fetches the structure and content of a GitHub repository.
    - **github_url**: URL of the GitHub repository.
    - **github_token**: Optional GitHub Personal Access Token for private repos or higher rate limits.
    """
    try:
        owner, repo_name = parse_github_url(request.github_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_token_from_request = request.github_token 

    try:
        # Initial call to fetch_repo_contents_recursive, client will be created inside
        repo_tree_root, all_files_data_flat = await fetch_repo_contents_recursive(
            owner, repo_name, user_provided_token=user_token_from_request
        )
    except HTTPException as e: # Re-raise HTTPExceptions from deeper calls
        raise e
    except Exception as e: # Catch any other unexpected errors during the process
        # Log this error on the server for debugging
        print(f"Unexpected server error processing {request.github_url}: {str(e)}")
        # Consider logging the full traceback here in a real production app
        # import traceback
        # print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred while processing the repository: {str(e)}")

    # --- Generate Text Tree ---
    text_tree_lines_list = [f"{repo_tree_root.name}/"] # Start with the root directory name
    if repo_tree_root.children: # Check if there are children to process
        num_top_level_children = len(repo_tree_root.children)
        for i, child_node in enumerate(repo_tree_root.children):
            generate_text_tree(child_node, text_tree_lines_list, is_last_sibling=(i == num_top_level_children - 1))
    text_tree_string = "\n".join(text_tree_lines_list)
    # --- End Text Tree Generation ---

    markdown_parts = [
        f"# Repository: {owner}/{repo_name}\n\n",
        f"## Repository Structure\n\n```text\n{text_tree_string}\n```\n\n",
        f"---\n\n## File Contents\n\n"
    ]
    
    # file_contents_map is built from all_files_data_flat which already has placeholders
    file_contents_map = {file_data['path']: file_data['content'] for file_data in all_files_data_flat}
    
    # Sort files by path for consistent markdown output
    all_files_data_flat.sort(key=lambda x: x['path'].lower()) 

    for file_data in all_files_data_flat:
        path = file_data['path']
        # content_for_md is already either actual text content or a placeholder string
        content_for_md = file_data['content'] 

        markdown_parts.append(f"### File: `{path}`\n")
        if is_binary_file(path): # If it's a binary file, just state the placeholder
            markdown_parts.append(f"{content_for_md}\n\n")
        else: # For text files, wrap content in a code block
            lang = path.split('.')[-1].lower() if '.' in path else ""
            # Basic language mapping for common types, can be expanded
            lang_map = {"js": "javascript", "py": "python", "md": "markdown", "yml": "yaml", "sh": "bash"}
            display_lang = lang_map.get(lang, lang if lang else "text")
            markdown_parts.append(f"```{(display_lang)}\n{content_for_md}\n```\n\n")
    
    combined_markdown = "".join(markdown_parts)

    if not repo_tree_root or not isinstance(repo_tree_root, FileNode):
         # This should ideally be caught earlier if repo fetching failed fundamentally
         raise HTTPException(status_code=500, detail="Failed to construct valid repository tree data after fetching.")

    return RepoDataResponse(
        tree=repo_tree_root, # FileNode.content here will also have placeholders for binary files
        all_files_markdown=combined_markdown,
        file_contents=file_contents_map # This map is used by Streamlit UI
    )

@app.get("/", summary="Root Endpoint", include_in_schema=False) # Exclude from OpenAPI docs if just a health check
async def read_root():
    return {"message": "GitHub to Markdown Converter Backend is running. POST to /api/fetch-repo to process a repository."}

# Optional: Add a simple health check endpoint
@app.get("/health", summary="Health Check", tags=["Health"])
async def health_check():
    return {"status": "healthy"}