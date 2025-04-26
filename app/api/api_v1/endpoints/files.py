from typing import Any, List
import os
import zipfile
import tempfile
import shutil
import subprocess
import uuid
from pathlib import Path
import logging
import glob
import re

from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import mimetypes

from app.core.auth import (
    get_current_active_user_cookie as get_current_active_user,
    get_current_admin_user_cookie as get_current_admin_user
)
from app.db.init_db import get_db
from app.core.config import settings
from app.models.models import User, Model
from app.api.api_v1.endpoints.explorer import check_path_permission

router = APIRouter()
logger = logging.getLogger(__name__)

# Path to a fallback image to use when a requested image is not found
FALLBACK_IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                                   "../../../../static/img/image-not-found.png")

def normalize_path(path):
    """
    Normalize file path considering case sensitivity issues and special characters.
    This function tries to find the actual case-sensitive path if the provided one doesn't exist.
    """
    if os.path.exists(path):
        return path
    
    # For debugging
    logger.info(f"Path doesn't exist, attempting normalization: {path}")
    
    # Try more aggressive path matching first
    drive, tail = os.path.splitdrive(path)
    # On Windows, ensure correct drive letter case
    if drive and os.name == 'nt':
        drive = drive.upper()
    
    # Split into components and reconstruct with correct case
    parts = Path(tail).parts
    current = Path(drive + os.sep if drive else os.sep)
    
    for part in parts:
        if not current.exists():
            logger.warning(f"Path component doesn't exist: {current}")
            return path
        
        # Skip empty parts
        if not part:
            continue
            
        # Special case for comparing with all lowercase or all uppercase
        lower_part = part.lower()
        upper_part = part.upper()
        
        # Try exact match first
        exact_match = current / part
        if exact_match.exists():
            current = exact_match
            continue
        
        # Try normalized path matching (case-insensitive)
        found = False
        try:
            for entry in os.scandir(current):
                # Try case-insensitive comparison first - fastest and most direct approach
                if entry.name.lower() == lower_part:
                    current = current / entry.name
                    found = True
                    break
            
            if not found:
                # Try more aggressive fuzzy matching on last component (for filenames with spaces/parentheses)
                # This is especially useful for image files that might have variants like (1), etc.
                logger.warning(f"No exact case-insensitive match for '{part}' in {current}")
                
                # Special case for last component (could be a file with different naming convention)
                if part == parts[-1]:
                    pattern = re.sub(r'[\s\(\)]+', '.*', re.escape(lower_part))
                    for entry in os.scandir(current):
                        if re.search(pattern, entry.name.lower()):
                            logger.info(f"Fuzzy matched {part} to {entry.name}")
                            current = current / entry.name
                            found = True
                            break
                
                if not found:
                    logger.warning(f"Component '{part}' not found in {current}")
                    return path
                
        except (PermissionError, FileNotFoundError) as e:
            logger.warning(f"Cannot list directory {current}: {str(e)}")
            return path
            
    # Check if the final path exists
    norm_path = str(current)
    if os.path.exists(norm_path):
        logger.info(f"Successfully normalized to existing path: {norm_path}")
        return norm_path
    else:
        logger.warning(f"Normalized path still doesn't exist: {norm_path}")
        return path

@router.get("/download")
async def download_file(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Download a file.
    Only allows downloading files that the current user has access to.
    Admin users can download any file.
    """
    # Normalize the path
    path = os.path.normpath(path)
    
    # Log the request for debugging
    logger.info(f"File download requested: {path}")
    
    # Check if the user has access to the path - now with await
    if not await check_path_permission(path, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this file",
        )
    
    # Check if the path exists
    if not os.path.exists(path):
        # Try to find the file with correct case
        original_path = path
        path = normalize_path(path)
        logger.info(f"Normalized path: {path} (original: {original_path})")
        
        # If it still doesn't exist, return fallback or error
        if not os.path.exists(path):
            logger.warning(f"File not found: {path}")
            
            # If it's an image request, return a fallback image
            mime_type, _ = mimetypes.guess_type(path)
            if mime_type and mime_type.startswith('image/'):
                if os.path.exists(FALLBACK_IMAGE_PATH):
                    logger.info(f"Returning fallback image for {path}")
                    return FileResponse(
                        path=FALLBACK_IMAGE_PATH,
                        filename=os.path.basename(path),
                        media_type="image/png"
                    )
            
            # Otherwise, return a 404
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
    
    # Check if the path is a file
    if os.path.isdir(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is a directory, not a file",
        )
    
    # Additional check if file actually exists and is readable
    try:
        with open(path, "rb") as f:
            # Just check if we can open the file
            pass
    except (PermissionError, IOError) as e:
        logger.error(f"Error accessing file {path}: {str(e)}")
        
        # If it's an image, return the fallback
        mime_type, _ = mimetypes.guess_type(path)
        if mime_type and mime_type.startswith('image/'):
            if os.path.exists(FALLBACK_IMAGE_PATH):
                logger.info(f"Returning fallback image due to access error for {path}")
                return FileResponse(
                    path=FALLBACK_IMAGE_PATH,
                    filename=os.path.basename(path),
                    media_type="image/png"
                )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to access file",
        )
    
    try:
        return FileResponse(
            path=path,
            filename=os.path.basename(path),
            media_type=mimetypes.guess_type(path)[0] or "application/octet-stream"
        )
    except Exception as e:
        logger.error(f"Error serving file {path}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error serving file: {str(e)}",
        )


@router.get("/download-zip")
async def download_directory_as_zip(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Download a directory as a zip file.
    Only allows downloading directories that the current user has access to.
    Admin users can download any directory.
    """
    # Normalize the path
    path = os.path.normpath(path)
    
    # Check if the path exists
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found",
        )
    
    # Check if the user has access to the path - now with await
    if not await check_path_permission(path, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this directory",
        )
    
    # Check if the path is a directory
    if not os.path.isdir(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        )
    
    # Create a temporary file for the zip
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_file.close()
    
    try:
        # Create the zip file
        with zipfile.ZipFile(temp_file.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, path)
                    zipf.write(file_path, arcname)
        
        # Return the zip file
        return FileResponse(
            path=temp_file.name,
            filename=f"{os.path.basename(path)}.zip",
            media_type="application/zip"
        )
    except Exception as e:
        # Clean up the temporary file if there's an error
        os.unlink(temp_file.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating zip file: {str(e)}",
        )


@router.get("/view")
async def view_text_file(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    View a text file in the browser.
    Only allows viewing files that the current user has access to.
    Admin users can view any file.
    """
    # Normalize the path
    path = os.path.normpath(path)
    
    # Check if the path exists
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    
    # Check if the user has access to the path - now with await
    if not await check_path_permission(path, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this file",
        )
    
    # Check if the path is a file
    if os.path.isdir(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is a directory, not a file",
        )
    
    # Check if the file is a text file
    _, ext = os.path.splitext(path)
    if ext.lower() not in settings.TEXT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not a text file",
        )
    
    # Read the file content
    try:
        with open(path, 'r', encoding='utf-8') as file:
            content = file.read()
        return {"content": content, "filename": os.path.basename(path)}
    except UnicodeDecodeError:
        # Try with a different encoding if utf-8 fails
        try:
            with open(path, 'r', encoding='latin-1') as file:
                content = file.read()
            return {"content": content, "filename": os.path.basename(path)}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error reading file: {str(e)}",
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading file: {str(e)}",
        )


@router.get("/preview-stl")
async def generate_stl_preview(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Generate a preview image for an STL file.
    Only allows previewing files that the current user has access to.
    Admin users can preview any file.
    """
    # Normalize the path
    path = os.path.normpath(path)
    
    # Check if the path exists
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    
    # Check if the user has access to the path
    if not check_path_permission(path, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this file",
        )
    
    # Check if the path is a file
    if os.path.isdir(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is a directory, not a file",
        )
    
    # Check if the file is an STL file
    _, ext = os.path.splitext(path)
    if ext.lower() != '.stl':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not an STL file",
        )
    
    # Create a unique filename for the preview
    filename = str(uuid.uuid4()) + ".png"
    preview_path = os.path.join(settings.STL_PREVIEW_DIR, filename)
    
    # Try to generate the preview using various tools
    # This requires external tools to be installed
    try:
        # First attempt: Try using Python libraries (requires numpy-stl and matplotlib)
        try:
            import numpy as np
            from stl import mesh
            import matplotlib.pyplot as plt
            from mpl_toolkits import mplot3d
            
            # Load the STL file
            stl_mesh = mesh.Mesh.from_file(path)
            
            # Create a new plot
            figure = plt.figure(figsize=(10, 10))
            axes = mplot3d.Axes3D(figure)
            
            # Add the STL mesh to the plot
            axes.add_collection3d(mplot3d.art3d.Poly3DCollection(stl_mesh.vectors))
            
            # Auto scale to the mesh size
            scale = stl_mesh.points.flatten()
            axes.auto_scale_xyz(scale, scale, scale)
            
            # Set background color
            axes.set_facecolor('white')
            
            # Remove axes
            plt.axis('off')
            
            # Save the preview
            plt.savefig(preview_path, bbox_inches='tight', dpi=100)
            plt.close(figure)
            
            return FileResponse(preview_path, media_type="image/png")
        
        except ImportError:
            # If the Python libraries are not available, try using external tools
            pass
        
        # Second attempt: Try using OpenSCAD (if installed)
        try:
            # Create a temporary SCAD file
            with tempfile.NamedTemporaryFile(suffix='.scad', delete=False) as scad_file:
                scad_content = f'import("{path}");\n'
                scad_file.write(scad_content.encode('utf-8'))
                scad_path = scad_file.name
            
            # Use OpenSCAD to generate the preview
            subprocess.run(
                ['openscad', '-o', preview_path, '--imgsize=800,800', scad_path],
                check=True
            )
            
            # Clean up temporary file
            os.unlink(scad_path)
            
            return FileResponse(preview_path, media_type="image/png")
        
        except (subprocess.SubprocessError, FileNotFoundError):
            # If OpenSCAD is not available, try another method
            pass
        
        # If all attempts fail, return an error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate STL preview. Required tools are not installed.",
        )
    
    except Exception as e:
        # If the preview file was created but there was an error, clean it up
        if os.path.exists(preview_path):
            os.unlink(preview_path)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating STL preview: {str(e)}",
        )