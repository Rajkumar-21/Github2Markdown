from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class RepoRequest(BaseModel):
    github_url: str
    github_token: Optional[str] = Field(None, description="Optional GitHub Personal Access Token provided by the user") # New field

class FileNode(BaseModel):
    name: str
    path: str
    type: str
    children: Optional[List['FileNode']] = None
    content: Optional[str] = None

FileNode.model_rebuild()

class RepoDataResponse(BaseModel):
    tree: FileNode
    all_files_markdown: str
    file_contents: Dict[str, str]