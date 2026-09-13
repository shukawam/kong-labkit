import subprocess
from pathlib import Path


class OpensslError(Exception):
    pass


def generate_cluster_cert(
    out_dir: Path, common_name: str, days: int = 1095
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    crt = out_dir / "cluster.crt"
    key = out_dir / "cluster.key"

    cmd = [
        "openssl",
        "req",
        "-new",
        "-x509",
        # Kong はパスフレーズ付きの鍵を読めないので -nodes は必須
        "-nodes",
        "-newkey",
        "rsa:2048",
        "-subj",
        f"/CN={common_name}/C=JP",
        "-keyout",
        str(key),
        "-out",
        str(crt),
        "-days",
        str(days),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise OpensslError(
            f"クラスタ証明書の生成に失敗しました（openssl の終了コード {result.returncode}）。\n"
            f"{result.stderr.strip()}"
        )

    key.chmod(0o600)
    return crt, key
