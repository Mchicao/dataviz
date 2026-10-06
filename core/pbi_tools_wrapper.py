"""
Wrapper para pbi-tools: Compila estructura PBIP a archivo PBIX.
"""

import os
import subprocess
import sys
from pathlib import Path

# Ruta a pbi-tools.exe (relativa al proyecto)
PROJECT_ROOT = Path(__file__).parent.parent
PBI_TOOLS_EXE = PROJECT_ROOT / "tools" / "pbi-tools" / "pbi-tools.exe"


def compile_pbip_to_pbix(
    pbip_folder: Path | str,
    output_pbix: Path | str | None = None,
    overwrite: bool = True,
) -> Path | None:
    """
    Compila una estructura PBIP (carpetas) a un archivo PBIX usando pbi-tools.

    Args:
        pbip_folder: Ruta a la carpeta base del proyecto PBIP (la carpeta que contiene .Report y .SemanticModel).
        output_pbix: Ruta opcional al archivo .pbix de salida. Si no se especifica, se crea junto al pbip_folder.
        overwrite: Si True, sobreescribe el archivo existente.

    Returns:
        Path al archivo PBIX generado, o None si falla.
    """
    pbip_folder = Path(pbip_folder)

    if not PBI_TOOLS_EXE.exists():
        print(f"❌ pbi-tools no encontrado en: {PBI_TOOLS_EXE}")
        print("   Ejecute: Invoke-WebRequest + Expand-Archive para instalar.")
        return None

    # Buscar la carpeta .Report (es la que pbi-tools necesita para compile)
    report_folder = None
    for item in pbip_folder.iterdir():
        if item.is_dir() and item.name.endswith(".Report"):
            report_folder = item
            break

    if not report_folder:
        print(f"❌ No se encontró carpeta .Report en: {pbip_folder}")
        return None

    # Determinar nombre de salida
    project_name = report_folder.name.replace(".Report", "")
    if output_pbix is None:
        output_pbix = pbip_folder / f"{project_name}.pbix"
    else:
        output_pbix = Path(output_pbix)

    # Construir comando
    cmd = [
        str(PBI_TOOLS_EXE),
        "compile",
        "-folder",
        str(report_folder),
        "-outPath",
        str(output_pbix),
    ]

    if overwrite:
        cmd.append("-overwrite")

    print(f"🔧 Ejecutando: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(pbip_folder),
            timeout=120,  # 2 minutos max
        )

        if result.returncode == 0:
            print(f"✅ PBIX generado: {output_pbix}")
            return output_pbix
        else:
            print(f"❌ Error en pbi-tools (código {result.returncode}):")
            print(result.stderr or result.stdout)
            return None

    except subprocess.TimeoutExpired:
        print("❌ Timeout: pbi-tools tardó demasiado.")
        return None
    except Exception as e:
        print(f"❌ Error ejecutando pbi-tools: {e}")
        return None


def hydrate_pbix(
    template_path: Path | str,
    pbip_folder: Path | str,
    output_pbix: Path | str,
) -> Path | None:
    """
    Hidrata un Template PBIX con el contenido de una estructura PBIP migrada.

    1. Extrae el Template (generando estructura válida pbi-tools).
    2. Sobrescribe report.json y model.bim con los del PBIP.
    3. Compila el resultado a un nuevo PBIX.
    """
    import shutil
    import tempfile

    template_path = Path(template_path)
    pbip_folder = Path(pbip_folder)
    output_pbix = Path(output_pbix)

    if not template_path.exists():
        print(f"❌ Template no encontrado: {template_path}")
        return None

    print(f"💧 Hidratando template: {template_path.name}...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # 1. Extraer Template
        extract_cmd = [
            str(PBI_TOOLS_EXE),
            "extract",
            str(template_path),
            "-extractFolder",
            str(temp_path),
        ]

        print("   📂 Extrayendo estructura base...")
        subprocess.run(extract_cmd, capture_output=True, check=True)

        # DEBUG: Listar contenido extraído
        print(f"   🔍 Contenido en {temp_path}:")
        for item in temp_path.iterdir():
            print(f"      - {item.name} ({'DIR' if item.is_dir() else 'FILE'})")

        # Identificar carpeta raíz del proyecto extraído
        extracted_root = None

        # Caso 0: La raíz es temp_path (extracción plana)
        if (
            (temp_path / ".pbixproj.json").exists()
            or (temp_path / "pbixproj.json").exists()
            or (temp_path / "Report").exists()
        ):
            extracted_root = temp_path
        else:
            # Caso 1: La raíz es una subcarpeta
            for item in temp_path.iterdir():
                if item.is_dir():
                    if (
                        (item / "Report").exists()
                        or (item / "pbixproj.json").exists()
                        or (item / ".pbixproj.json").exists()
                    ):
                        extracted_root = item
                        break

            # Caso 2: Fallback
            if not extracted_root:
                dirs = [x for x in temp_path.iterdir() if x.is_dir()]
                if dirs:
                    extracted_root = dirs[0]

        if not extracted_root:
            print("   ❌ Error: No se pudo identificar la raíz del proyecto extraído.")
            return None

        # Visual aid
        root_name = extracted_root.name if extracted_root != temp_path else "[TEMP_ROOT]"
        print(f"   📍 Raíz identificada: {root_name}")

        # 2. Inyectar Contenido (Reporte)
        # Buscar source report.json
        src_report = None
        for item in pbip_folder.iterdir():
            if item.name.endswith(".Report"):
                src_report = item / "report.json"
                break

        if src_report and src_report.exists():
            # Destino: Report/report.json o Report/Layout
            dest_report_dir = extracted_root / "Report"
            if not dest_report_dir.exists():
                dest_report_dir.mkdir(parents=True, exist_ok=True)

            # Escribir report.json
            shutil.copy(src_report, dest_report_dir / "report.json")

            # Si existe "Layout", borrarlo para obligar a usar report.json (o reemplazarlo)
            # pbi-tools legacy usa Layout.
            layout_path = dest_report_dir / "Layout"
            if layout_path.exists():
                os.remove(layout_path)
                shutil.copy(src_report, layout_path)

            print("   ✅ Reporte inyectado.")

        # 3. Inyectar Contenido (Modelo)
        # Buscar source model.bim
        src_model = None
        for item in pbip_folder.iterdir():
            if item.name.endswith(".SemanticModel"):
                src_model = item / "model.bim"
                break

        if src_model and src_model.exists():
            dest_model_dir = extracted_root / "Model"
            if not dest_model_dir.exists():
                dest_model_dir.mkdir(parents=True, exist_ok=True)
            else:
                # LIMPIEZA CRÍTICA: Borrar contenido existente (ej: TMDL)
                for child in dest_model_dir.iterdir():
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)

            # pbi-tools usa database.json para TOM
            shutil.copy(src_model, dest_model_dir / "database.json")
            print("   ✅ Modelo inyectado (database.json).")

        # 4. Compilar
        print("   🔨 Compilando PBIX final...")
        compile_cmd = [
            str(PBI_TOOLS_EXE),
            "compile",
            "-folder",
            str(extracted_root),
            "-outPath",
            str(output_pbix),
            "-overwrite",
        ]

        # Forzar formato PBIT si la extensión lo indica
        if str(output_pbix).lower().endswith(".pbit"):
            compile_cmd.extend(["-format", "PBIT"])

        res = subprocess.run(compile_cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"   ✨ Éxito: {output_pbix}")
            return output_pbix
        else:
            print(f"   ❌ Falla compilación (Code {res.returncode}):")
            print("   --- STDERR ---")
            print(res.stderr)
            print("   --- STDOUT ---")
            print(res.stdout)
            return None


if __name__ == "__main__":
    # Test
    if len(sys.argv) < 2:
        print("Uso: python pbi_tools_wrapper.py <ruta_carpeta_pbip>")
        sys.exit(1)

    result = compile_pbip_to_pbix(sys.argv[1])
    sys.exit(0 if result else 1)
