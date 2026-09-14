# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.9", "jinja2>=3.1", "pyyaml>=6.0", "click>=8.1"]
# ///
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# uv run generator/gen.py で起動すると sys.path[0] が generator/ になり、
# generator パッケージとして自分を import できなくなる
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from pydantic import ValidationError

from generator.certs import generate_cluster_cert
from generator.context import build_context
from generator.render import render_all
from generator.schema import load_spec

# --force でも決して上書きしない。API キー、Konnect 登録済みの証明書、顧客向け成果物
PROTECTED_PATHS = (".env", ".certs", "docs")


class TargetNotEmptyError(Exception):
    pass


class DirtyWorktreeError(Exception):
    pass


@dataclass
class WriteResult:
    created: list[Path] = field(default_factory=list)
    overwritten: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


def _is_protected(relpath: str) -> bool:
    head = Path(relpath).parts[0]
    return head in PROTECTED_PATHS


def _is_inside_protected_dir(relpath: str) -> bool:
    parts = Path(relpath).parts
    return len(parts) > 1 and parts[0] in PROTECTED_PATHS


def check_git_clean(out: Path) -> None:
    if not out.exists():
        return  # 生成先がまだ無いなら未管理と同じ扱いでよい
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return  # git 管理下でなければ何も言わない
    if result.stdout.strip():
        raise DirtyWorktreeError(
            f"{out} に未コミットの変更があります。"
            "--force は生成物を上書きするため、先に commit か stash をしてください。"
        )


def write_files(out: Path, files: dict[str, str], force: bool) -> WriteResult:
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        raise TargetNotEmptyError(
            f"{out} は空ではありません。既存の環境を更新する場合は --force を付けてください。"
        )

    result = WriteResult()
    for relpath, body in sorted(files.items()):
        target = out / relpath
        if target.exists():
            if _is_protected(relpath):
                result.skipped.append(target)
                continue
            result.overwritten.append(target)
        elif _is_inside_protected_dir(relpath) and target.parent.exists() and any(
            target.parent.iterdir()
        ):
            # docs/ に人が書いたものがある状態で .gitkeep だけ足すのは無意味
            result.skipped.append(target)
            continue
        else:
            result.created.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return result


@click.command()
@click.argument("env_yaml", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--out", required=True, type=click.Path(path_type=Path), help="生成先")
@click.option("--force", is_flag=True, help="既存の生成物を上書きする")
def main(env_yaml: Path, out: Path, force: bool) -> None:
    try:
        spec = load_spec(env_yaml)
    except ValidationError as e:
        for error in e.errors():
            click.echo(click.style(str(error["msg"]).removeprefix("Value error, "), fg="red"), err=False)
        sys.exit(1)

    for warning in spec.warnings():
        click.echo(click.style(f"警告: {warning}", fg="yellow"))

    ctx = build_context(spec)

    try:
        if force:
            check_git_clean(out)

        out.mkdir(parents=True, exist_ok=True)
        result = write_files(out, render_all(ctx), force=force)
    except (DirtyWorktreeError, TargetNotEmptyError) as e:
        click.echo(click.style(str(e), fg="red"))
        sys.exit(1)

    # 入力そのものを残すことで、後から何を選んだかを compose から逆算しなくて済む
    shutil.copyfile(env_yaml, out / "env.yaml")

    if spec.control_plane.value == "konnect":
        if not (out / ".certs" / "cluster.crt").exists():
            generate_cluster_cert(out / ".certs", common_name=ctx.kong.cp_name)
            result.created.append(out / ".certs" / "cluster.crt")
    else:
        cert_dir = out / "config" / "kong" / "certs"
        if not (cert_dir / "tls.crt").exists():
            generate_cluster_cert(cert_dir, common_name="kong-cluster", basename="tls")
            result.created.append(cert_dir / "tls.crt")

    for label, paths, color in (
        ("作成", result.created, "green"),
        ("上書き", result.overwritten, "yellow"),
        ("保護してスキップ", result.skipped, "cyan"),
    ):
        for path in paths:
            click.echo(click.style(f"{label}: {path.relative_to(out)}", fg=color))


if __name__ == "__main__":
    main()
