"""
CI/CD Module for Power BI.
Handles:
1. Authentication (MSAL Device Code)
2. PBIP Compression (if needed)
3. PBIX/PBIP Upload (Power BI REST API)
4. Audit Trigger (dscmd)
"""

import logging
import os
import time
import zipfile
from pathlib import Path

import msal
import requests

logger = logging.getLogger(__name__)


class PowerBIDeployer:
    # Azure AD Constants for Power BI Public App
    CLIENT_ID = "c0d2a505-13b8-4ae3-aa9e-169b0c141f48"  # Power BI Service (Public)
    AUTHORITY_URL = "https://login.microsoftonline.com/common"
    SCOPE = [
        "https://analysis.windows.net/powerbi/api/Report.ReadWrite.All",
        "https://analysis.windows.net/powerbi/api/Dataset.ReadWrite.All",
    ]

    API_BASE = "https://api.powerbi.com/v1.0/myorg"

    def __init__(self):
        self.app = msal.PublicClientApplication(self.CLIENT_ID, authority=self.AUTHORITY_URL)
        self.token: str | None = None

    def authenticate(self) -> bool:
        """
        Performs interactive authentication using Device Code Flow.
        Returns True if successful.
        """
        # 1. Try cache first
        accounts = self.app.get_accounts()
        if accounts:
            logger.info(f"Account found in cache: {accounts[0]['username']}")
            result = self.app.acquire_token_silent(self.SCOPE, account=accounts[0])
            if result and "access_token" in result:
                self.token = result["access_token"]
                return True

        # 2. Interactive Device Code Flow
        logger.info("Initiating Device Code Flow...")
        flow = self.app.initiate_device_flow(scopes=self.SCOPE)
        if "user_code" not in flow:
            logger.error("Failed to create device flow")
            return False

        print("\n⚠️  AUTHENTICATION REQUIRED ⚠️")
        print(f"Open browser at: {flow['verification_uri']}")
        print(f"Enter Code: {flow['user_code']}")
        print("Waiting for login...")

        result = self.app.acquire_token_by_device_flow(flow)

        if "access_token" in result:
            self.token = result["access_token"]
            logger.info("Authentication successful!")
            return True
        else:
            logger.error(f"Authentication failed: {result.get('error_description')}")
            return False

    def deploy_pbip(self, pbip_folder: str, workspace_id: str, display_name: str) -> bool:
        """
        Deploys a PBIP folder to a workspace.
        Strategy: Compresses PBIP folder to a pseudo-PBIX (ZIP) and uploads via Import API.
        Note: The Import API validates the internal structure.
        If strict PBIP structure isn't supported as ZIP-Import yet by public API,
        we might need to compile to actual PBIX using external tools or wait for Fabric API Definitions support.

        For PPU/Pro standard, submitting a PBIX binary is the most robust method via REST API.
        We will assume for this V1 script that the user provides a PBIX or we zip it as PBIX.
        """
        if not self.token:
            logger.error("Not authenticated.")
            return False

        pbip_path = Path(pbip_folder)
        if not pbip_path.exists():
            logger.error(f"Path not found: {pbip_folder}")
            return False

        # Determine if it's a file (PBIX) or folder (PBIP)
        upload_file_path = str(pbip_path)
        temp_zip = None

        if pbip_path.is_dir():
            logger.info("Detected PBIP Folder. Compressing to temporary PBIX...")
            temp_zip = f"{display_name}.pbix"
            self._compress_folder_to_zip(pbip_path, temp_zip)
            upload_file_path = temp_zip

        try:
            url = f"{self.API_BASE}/groups/{workspace_id}/imports?datasetDisplayName={display_name}&nameConflict=Abort"
            # Note: For overwrite, usually we use naming conflict param or delete previous.
            # 'nameConflict=CreateOrOverwrite' is supported in some versions, or 'Abort', 'Ignore'.

            # Refined URL for Overwrite if supported or handle manually
            # "nameConflict" parameter valid values: Ignore, Abort, Overwrite, CreateOrOverwrite
            url = f"{self.API_BASE}/groups/{workspace_id}/imports?datasetDisplayName={display_name}&nameConflict=CreateOrOverwrite"

            headers = {"Authorization": f"Bearer {self.token}"}

            # Using multipart/form-data
            with open(upload_file_path, "rb") as f:
                files = {"file": (display_name, f, "application/octet-stream")}
                logger.info(f"Uploading {upload_file_path} to Workspace {workspace_id}...")
                response = requests.post(url, headers=headers, files=files)

            if response.status_code in [200, 201, 202]:
                logger.info("Upload initiated successfully.")
                import_id = response.json().get("id")
                return self._wait_for_import(workspace_id, import_id)
            else:
                logger.error(f"Upload failed: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Deployment error: {e}")
            return False
        finally:
            if temp_zip and os.path.exists(temp_zip):
                os.remove(temp_zip)

    def _compress_folder_to_zip(self, folder_path: Path, output_path: str):
        """Compresses a folder (PBIP) into a ZIP file (renamed to .pbix)."""
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, folder_path)
                    zipf.write(file_path, arcname)

    def _wait_for_import(self, workspace_id: str, import_id: str) -> bool:
        """Polls the import status until completion."""
        url = f"{self.API_BASE}/groups/{workspace_id}/imports/{import_id}"
        headers = {"Authorization": f"Bearer {self.token}"}

        logger.info(f"Waiting for import {import_id} to complete...")

        for _ in range(30):  # 30 attempts * 2 seconds = 60s timeout roughly
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                logger.warning("Could not get import status.")
                time.sleep(2)
                continue

            state = response.json().get("importState")
            logger.info(f"Import State: {state}")

            if state == "Succeeded":
                return True
            elif state == "Failed":
                logger.error("Import failed on server side.")
                return False

            time.sleep(2)

        logger.error("Import timed out.")
        return False
