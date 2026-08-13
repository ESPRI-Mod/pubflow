from pathlib import Path


def rewrite_mapfile(source, destination, path_mappings):
    """
    Create a publisher-ready mapfile by rewriting file paths.
    The source mapfile is never modified.
    """
    source = Path(source)
    destination = Path(destination)

    with source.open() as src, destination.open("w") as dst:
        for line in src:
            stripped = line.strip()
            if not stripped:
                dst.write(line)
                continue
            fields = stripped.split("|")
            if len(fields) < 3:
                dst.write(line)
                continue
            file_path = fields[1].strip()
            for mapping in path_mappings:
                source_root = mapping["from"]
                target_root = mapping["to"]
                if file_path.startswith(source_root + "/"):
                    file_path = (target_root + file_path[len(source_root):])
                    break
            fields[1] = f" {file_path} "
            dst.write("|".join(fields) + "\n")
