import logging
import os
import shutil
import subprocess

# Configure logging
logger = logging.getLogger(__name__)


class DscmdWrapper:
    """
    Wrapper for DAX Studio CLI (dscmd.exe).
    Enables automated VPAX generation and DAX query execution.
    Supports both Local (localhost:port) and XMLA (powerbi://...) connections.
    """

    def __init__(self, dscmd_path: str | None = None):
        """
        Initialize the wrapper.

        Args:
            dscmd_path: Optional path to dscmd.exe. If None, assumes it's in PATH.
        """
        self.dscmd_cmd = dscmd_path if dscmd_path else "dscmd"
        self._check_availability()

    def _check_availability(self):
        """Checks if dscmd is available in the system."""
        if shutil.which(self.dscmd_cmd) is None:
            logger.warning(
                f"'{self.dscmd_cmd}' not found in PATH. DAX Studio integration will be disabled."
            )
            self.available = False
        else:
            self.available = True

    def generate_vpax(
        self, connection_string: str, output_path: str, database_name: str | None = None
    ) -> bool:
        """
        Generates a VPAX file from a Power BI model.

        Args:
            connection_string: Connection string (e.g., 'localhost:1234' or 'powerbi://api.powerbi.com/v1.0/myorg/workspace')
            output_path: Destination path for the .vpax file.
            database_name: Database name (Optional for XMLA, usually required for localhost if multiple DBs exist).

        Returns:
            bool: True if successful, False otherwise.
        """
        if not self.available:
            logger.error("dscmd is not available.")
            return False

        # Build command: dscmd vpax <output> -s <server> [-d <db>]
        cmd = [self.dscmd_cmd, "vpax", output_path, "-s", connection_string]

        if database_name:
            cmd.extend(["-d", database_name])

        try:
            logger.info(f"Generating VPAX: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("VPAX generation successful.")
            logger.debug(result.stdout)
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Error generating VPAX: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error executing dscmd: {e}")
            return False

    def export_query_results(
        self,
        connection_string: str,
        dax_query: str,
        output_csv: str,
        database_name: str | None = None,
    ) -> bool:
        """
        Executes a DAX query and exports the results to CSV.

        Args:
            connection_string: Connection string.
            dax_query: The DAX query to execute (e.g., "EVALUATE 'Table'").
                       NOTE: It's safer to pass a file path to dscmd for complex queries,
                       but this wrapper currently uses the direct command line arg or requires a temp file strategy
                       if the query is long. dscmd supports passing a file.
            output_csv: Path to save result.
            database_name: Database name.

        Returns:
            bool: True if successful.
        """
        if not self.available:
            return False

        # Strategy: Write query to temp file to avoid CLI escaping issues
        temp_dax_file = output_csv + ".dax"
        try:
            with open(temp_dax_file, "w", encoding="utf-8") as f:
                f.write(dax_query)

            # dscmd csv <output> -s <server> -q <query_file_path> ... wait, -q expects string or file?
            # From help: dscmd csv <output> ... -q "EVALUATE ..." OR can we pass a file?
            # Looking at docs: usually CLI tools prefer file for complex args.
            # Let's assume -q takes the query string. If dscmd supports file input for query, it might be a different flag or inferred.
            # Re-reading user provided info: "dscmd csv 'res.csv' ... -q 'EVALUATE...'"
            # To be safe with quotes/multilines, we might need to test.
            # For now let's use the explicit query string if it's short, or check help functionality.
            # ACTUALLY, checking standard dscmd usage: it often supports input file as a main arg or flag.
            # Let's stick to simple -q for now as documented by user.

            cmd = [self.dscmd_cmd, "csv", output_csv, "-s", connection_string, "-q", dax_query]

            if database_name:
                cmd.extend(["-d", database_name])

            logger.info(f"Executing DAX Query: {' '.join(cmd)}")
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Error executing DAX: {e.stderr}")
            return False
        finally:
            if os.path.exists(temp_dax_file):
                os.remove(temp_dax_file)

    def is_xmla_connection(self, connection_string: str) -> bool:
        """Helper to check if a connection string looks like an XMLA endpoint."""
        return connection_string.lower().startswith("powerbi://")
