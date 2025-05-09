from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class RepoRequest(BaseModel):
    github_url: str
    github_token: Optional[str] = Field(None, description="Optional GitHub Personal Access Token provided by the user")

class FileNode(BaseModel):
    name: str
    path: str
    type: str # 'file' or 'dir'
    children: Optional[List['FileNode']] = None
    content: Optional[str] = None # For text files or placeholder for binary

FileNode.model_rebuild() # For recursive Pydantic models

class RepoDataResponse(BaseModel):
    tree: FileNode # Hierarchical tree structure for UI
    all_files_markdown: str # Combined markdown including text tree and file contents
    file_contents: Dict[str, str] # Map of file_path: content (or placeholder)