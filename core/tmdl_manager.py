import os


class TMDLManager:
    def __init__(self, semantic_model_path: str):
        self.tables_path = os.path.join(semantic_model_path, "definition", "tables")

    def inject_measures(self, table_name: str, measures: list[dict]):
        """
        Injects measures into a specific table's TMDL file.
        measures: list of dict {'name': str, 'expression': str, 'folder': str, 'description': str}
        """
        # Find the .tmdl file for the table
        # Typically it's named TableName.tmdl, but characters might be escaped or different.
        # We'll search for the file.

        target_file = None
        # Simple search first
        potential_path = os.path.join(self.tables_path, f"{table_name}.tmdl")
        if os.path.exists(potential_path):
            target_file = potential_path
        else:
            # Iterate to find
            if os.path.exists(self.tables_path):
                for f in os.listdir(self.tables_path):
                    if f.endswith(".tmdl"):
                        # We could parse the 'table Name' line to be sure,
                        # but usually filename matches roughly.
                        # For now let's assume filename match or simple scan if needed.
                        pass

        if not target_file:
            print(f"Error: Could not find TMDL file for table '{table_name}' in {self.tables_path}")
            return False

        try:
            with open(target_file, encoding="utf-8") as f:
                content = f.read()

            # TMDL uses indentation. We need to append the measure inside the table block.
            # Measures are usually indented by 1 tab or 4 spaces.
            # Let's verify indentation from the first indented line (if any).

            # Simple heuristic: Append to the end of the file, indented.
            # If the file ends with a blank line, great.

            indent = "\t"  # Default standard for TMDL often

            # Look for existing indentation
            lines = content.splitlines()
            for line in lines:
                if line.startswith("    "):
                    indent = "    "
                    break
                if line.startswith("\t"):
                    indent = "\t"
                    break

            new_content = content.rstrip() + "\n\n"

            for m in measures:
                # Format:
                # measure 'Name' =
                #     Expression...
                #     lineageTag: ...
                #     displayFolder: ...

                # Check if measure already exists to avoid duplication errors?
                # A simple check in text
                if f"measure '{m['name']}'" in content or f'measure "{m["name"]}"' in content:
                    print(f"Skipping {m['name']}, already exists.")
                    continue

                dax_formatted = m["expression"].replace("\n", f"\n{indent}{indent}")

                new_content += f"{indent}measure '{m['name']}' = {dax_formatted}\n"
                if m.get("folder"):
                    new_content += f"{indent}{indent}displayFolder: {m['folder']}\n"

                # Add a blank line between measures
                new_content += "\n"

            with open(target_file, "w", encoding="utf-8") as f:
                f.write(new_content)

            return True

        except Exception as e:
            print(f"Error injecting TMDL: {e}")
            return False

    def remove_measures(self, table_name: str):
        """
        Removes alert measures from a table's TMDL file.
        """
        target_file = os.path.join(self.tables_path, f"{table_name}.tmdl")
        if not os.path.exists(target_file):
            return False

        try:
            with open(target_file, encoding="utf-8") as f:
                lines = f.readlines()

            new_lines = []
            skip_until_blank = False
            removed_count = 0

            for line in lines:
                # Heuristic: Find measure lines that the tool creates
                if ("measure 'Alerta_" in line or 'measure "Alerta_' in line) and " =" in line:
                    skip_until_blank = True
                    removed_count += 1
                    continue

                if skip_until_blank:
                    if line.strip() == "":
                        skip_until_blank = False
                    continue

                new_lines.append(line)

            if removed_count > 0:
                with open(target_file, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                print(f"Removed {removed_count} alert measures from {table_name}")
                return True

            return False

        except Exception as e:
            print(f"Error removing from TMDL: {e}")
            return False

    def get_model_metadata(self):
        """
        Returns { table_name: { 'columns': [col1...], 'measures': [m1...] } }
        """
        metadata = {}
        if not os.path.exists(self.tables_path):
            return metadata

        for filename in os.listdir(self.tables_path):
            if filename.endswith(".tmdl"):
                # Use filename as backup table name, but we try to find 'table Name' line
                table_name = filename[:-5]
                cols = []
                meas = []
                filepath = os.path.join(self.tables_path, filename)
                try:
                    with open(filepath, encoding="utf-8") as f:
                        for line in f:
                            # Clean whitespace
                            stripped = line.strip()
                            # Table name line: table Name
                            if stripped.startswith("table "):
                                table_name = stripped[6:].strip("'\"")

                            # Column: column 'Name'
                            if stripped.startswith("column "):
                                c_name = stripped[7:].split("=")[0].strip("'\" ")
                                if c_name:
                                    cols.append(c_name)

                            # Measure: measure 'Name'
                            if stripped.startswith("measure "):
                                m_name = stripped[8:].split("=")[0].strip("'\" ")
                                if m_name:
                                    meas.append(m_name)
                except Exception as e:
                    print(f"Error reading TMDL {filename}: {e}")

                metadata[table_name] = {
                    "columns": sorted(list(set(cols))),
                    "measures": sorted(list(set(meas))),
                }
        return metadata
