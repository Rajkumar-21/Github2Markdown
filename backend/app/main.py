import os
import base64
import re
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from .models import RepoRequest, FileNode, RepoDataResponse

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GITHUB_API_BASE_URL = "https://api.github.com"
BACKEND_GITHUB_TOKEN_ENV = os.getenv("GITHUB_TOKEN")

# --- List of common binary/image file extensions to ignore content for ---
# This list can be expanded
BINARY_FILE_EXTENSIONS = {
    # Images
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg', '.ico',
    # Audio
    '.mp3', '.wav', '.aac', '.ogg', '.flac',
    # Video
    '.mp4', '.mov', '.avi', '.mkv', '.webm',
    # Archives
    '.zip', '.tar', '.gz', '.rar', '.7z',
    # Documents (that are often binary)
    '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx',
    # Executables / Libraries
    '.exe', '.dll', '.so', '.dylib', '.jar',
    # Other
    '.pyc', '.pyo', '.class', '.o', '.a', '.obj',
    '.eot', '.otf', '.ttf', '.woff', '.woff2', # Fonts
    '.DS_Store'
}

def is_binary_file(file_path: str) -> bool:
    """Checks if a file is likely binary based on its extension."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in BINARY_FILE_EXTENSIONS

async def get_github_api_headers(user_provided_token: Optional[str] = None) -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    token_to_use = user_provided_token or BACKEND_GITHUB_TOKEN_ENV
    if token_to_use:
        headers["Authorization"] = f"token {token_to_use}"
    return headers

def parse_github_url(url: str) -> tuple[str, str]:
    match = re.match(r"https://github\.com/([^/]+)/([^/.]+?)(?:\.git)?(?:/tree/[^/]+(?:/.*)?)?/?$", url)
    if not match:
        raise ValueError("Invalid GitHub URL format.")
    owner, repo = match.groups()
    return owner, repo

def generate_text_tree(node: FileNode, tree_lines: List[str] = None, is_last: bool = True, prefix: str = ""):
    if tree_lines is None: tree_lines = []
    connector = "└── " if is_last else "├── "
    tree_lines.append(f"{prefix}{connector}{node.name}{'/' if node.type == 'dir' else ''}")
    if node.type == 'dir' and node.children:
        new_prefix = prefix + ("    " if is_last else "│   ")
        num_children = len(node.children)
        for i, child in enumerate(node.children):
            generate_text_tree(child, tree_lines, i == num_children - 1, new_prefix)
    return tree_lines


async def fetch_repo_contents_recursive(
    owner: str, repo: str, path: str = "",
    client: httpx.AsyncClient = None, user_provided_token: Optional[str] = None
) -> tuple[FileNode, List[dict]]:
    current_headers = await get_github_api_headers(user_provided_token)
    if client is None:
        async with httpx.AsyncClient() as new_client:
            return await fetch_repo_contents_recursive(owner, repo, path, new_client, user_provided_token)

    url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{path}"
    try:
        response = await client.get(url, headers=current_headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        # ... (error handling as before, no changes needed here for binary files) ...
        detail_message = f"GitHub API error for {url}: {e.response.text}"
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Repository or path not found: {owner}/{repo}/{path}")
        elif e.response.status_code == 403:
            rate_limit_remaining = e.response.headers.get("X-RateLimit-Remaining")
            token_in_use = user_provided_token or BACKEND_GITHUB_TOKEN_ENV
            if rate_limit_remaining == "0":
                detail_message = "GitHub API rate limit exceeded. "
                if not token_in_use: detail_message += "Try again later or provide a GitHub token for higher limits."
                else: detail_message += "Try again later (even with a token)."
            elif not token_in_use:
                detail_message = f"Access forbidden to {owner}/{repo}/{path}. This could be a private repository (requires a GitHub token) or a rate limit issue."
            else: 
                detail_message = f"Access forbidden to {owner}/{repo}/{path} using the provided token. Check token permissions or if it's a rate limit issue."
            raise HTTPException(status_code=403, detail=detail_message)
        raise HTTPException(status_code=e.response.status_code, detail=detail_message)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Network error while contacting GitHub API: {str(e)}")


    items_data = response.json()
    
    if isinstance(items_data, dict) and items_data.get('type') == 'file': # Path points to a single file
        item = items_data
        item_path = item['path']
        file_content_str = f"[Content of binary file: {item_path}]" # Default for binary
        
        if not is_binary_file(item_path): # Only fetch content if not binary
            try:
                if item.get('download_url'):
                    content_response = await client.get(item['download_url'], headers=current_headers)
                    content_response.raise_for_status()
                    file_content_str = content_response.content.decode('utf-8', errors='replace')
                # No 'else if content in item' here, as download_url is preferred for raw content
            except Exception as e_content:
                file_content_str = f"[Error fetching/decoding content for {item_path}: {str(e_content)}]"
        
        single_file_node = FileNode(name=item['name'], path=item_path, type='file', content=file_content_str)
        return single_file_node, [{"path": item_path, "content": file_content_str}]

    if not isinstance(items_data, list):
         raise HTTPException(status_code=400, detail=f"Unexpected response from GitHub API for path: {path}.")

    root_name = path.split('/')[-1] if path else repo
    current_node = FileNode(name=root_name, path=path, type='dir', children=[])
    all_files_data_flat = []

    for item in items_data:
        item_path = item['path']
        if item['type'] == 'file':
            file_content_str = f"[Content of binary/image file: {item_path}]" # Default for binary

            if not is_binary_file(item_path): # Process content only if not binary
                try:
                    if item.get('download_url'):
                        content_response = await client.get(item['download_url'], headers=current_headers)
                        content_response.raise_for_status()
                        # Try to decode as UTF-8, replace errors for robustness
                        file_content_str = content_response.content.decode('utf-8', errors='replace')
                    elif 'content' in item and item.get('encoding') == 'base64': # Fallback for smaller files via content API
                        # This path is less likely if download_url is present, but good to have
                        file_content_str = base64.b64decode(item['content']).decode('utf-8', errors='replace')
                    else:
                        file_content_str = f"[Text content not available or not fetched for {item_path}.]"
                except Exception as e_file:
                     file_content_str = f"[Error fetching/decoding text content for {item_path}: {str(e_file)}]"
            
            # Add to tree node and flat list with potentially modified content (placeholder or actual)
            current_node.children.append(FileNode(name=item['name'], path=item_path, type='file', content=file_content_str))
            all_files_data_flat.append({"path": item_path, "content": file_content_str})

        elif item['type'] == 'dir':
            subtree_node, subtree_files_data = await fetch_repo_contents_recursive(
                owner, repo, item_path, client, user_provided_token
            )
            current_node.children.append(subtree_node)
            all_files_data_flat.extend(subtree_files_data)
            
    if current_node.children:
        current_node.children.sort(key=lambda x: (x.type != 'dir', x.name.lower()))

    return current_node, all_files_data_flat

@app.post("/api/fetch-repo", response_model=RepoDataResponse)
async def fetch_repo_api(request: RepoRequest):
    try:
        owner, repo_name = parse_github_url(request.github_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_token_from_request = request.github_token 

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            repo_tree_root, all_files_data = await fetch_repo_contents_recursive(
                owner, repo_name, client=client, user_provided_token=user_token_from_request
            )
        except HTTPException as e:
            raise e
        except Exception as e:
            print(f"Unexpected error in fetch_repo_contents_recursive: {e}")
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

    text_tree_lines = [f"{repo_tree_root.name}/"]
    if repo_tree_root.children:
        num_top_level_children = len(repo_tree_root.children)
        for i, child in enumerate(repo_tree_root.children):
            generate_text_tree(child, tree_lines=text_tree_lines, is_last=(i == num_top_level_children - 1), prefix="")
    text_tree_string = "\n".join(text_tree_lines)

    markdown_parts = [
        f"# Repository: {owner}/{repo_name}\n\n",
        f"## Repository Structure\n\n```text\n{text_tree_string}\n```\n\n",
        f"---\n\n## File Contents\n\n"
    ]
    file_contents_map = {} # This will store path -> content (or placeholder for binary)
    all_files_data.sort(key=lambda x: x['path'].lower())

    for file_data in all_files_data:
        path = file_data['path']
        content_for_md = file_data['content'] # This content is already a placeholder if binary
        file_contents_map[path] = content_for_md # Store for UI display

        markdown_parts.append(f"### File: `{path}`\n")
        if is_binary_file(path): # If it's a binary file, just state it, don't wrap in code block
            markdown_parts.append(f"{content_for_md}\n\n")
        else: # For text files, wrap content in a code block
            lang = path.split('.')[-1] if '.' in path else ""
            markdown_parts.append(f"```{(lang if lang else 'text')}\n{content_for_md}\n```\n\n")
    
    combined_markdown = "".join(markdown_parts)

    if not repo_tree_root or not isinstance(repo_tree_root, FileNode):
         raise HTTPException(status_code=500, detail="Failed to construct valid repository tree data.")

    return RepoDataResponse(
        tree=repo_tree_root, # FileNode.content here will also have placeholders for binary files
        all_files_markdown=combined_markdown,
        file_contents=file_contents_map # This map is used by Streamlit UI
    )

@app.get("/")
async def root():
    return {"message": "GitHub to Markdown Converter Backend. POST to /api/fetch-repo."}