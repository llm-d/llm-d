# install_prerequisites.py
import argparse
import glob
import logging
import os
import subprocess
import sys
import time

# --- Configuration ---
WHEELS_CACHE_HOME = os.environ.get("WHEELS_CACHE_HOME", "/workspace/wheels_cache")
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
UCX_DIR = os.path.join('/tmp', 'ucx_source')
NIXL_DIR = os.path.join('/tmp', 'nixl_source')
UCX_INSTALL_DIR = os.path.join('/tmp', 'ucx_install')
UCX_REPO_URL = 'https://github.com/openucx/ucx.git'
NIXL_REPO_URL = 'https://github.com/ai-dynamo/nixl.git'

# Default versions (can be overridden via CLI args or env vars)
DEFAULT_UCX_VERSION = os.environ.get("UCX_VERSION", "v1.19.x")
DEFAULT_NIXL_VERSION = os.environ.get("NIXL_VERSION", "0.6.1")

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds

logger = logging.getLogger("install_nixl")


# --- Helper Functions ---
def run_command(command, cwd='.', env=None, dry_run=False):
    """Run a shell command and check for errors.

    Args:
        command: Command and arguments as a list of strings.
        cwd: Working directory for the command.
        env: Environment variables for the command.
        dry_run: If True, log the command without executing it.
    """
    cmd_str = ' '.join(command)
    logger.info("Running command: %s in '%s'", cmd_str, cwd)
    if dry_run:
        logger.info("[DRY RUN] Skipped: %s", cmd_str)
        return
    subprocess.check_call(command, cwd=cwd, env=env)


def run_command_with_retry(command, cwd='.', env=None, dry_run=False,
                           max_retries=MAX_RETRIES):
    """Run a command with retry logic for transient network failures.

    Uses exponential backoff between retries. Intended for network-dependent
    operations such as git clone, git fetch, and pip install/wheel.

    Args:
        command: Command and arguments as a list of strings.
        cwd: Working directory for the command.
        env: Environment variables for the command.
        dry_run: If True, log the command without executing it.
        max_retries: Maximum number of retry attempts.
    """
    cmd_str = ' '.join(command)
    for attempt in range(1, max_retries + 1):
        try:
            run_command(command, cwd=cwd, env=env, dry_run=dry_run)
            return
        except subprocess.CalledProcessError as exc:
            if attempt == max_retries:
                logger.error(
                    "Command failed after %d attempts: %s", max_retries, cmd_str
                )
                raise
            wait_time = RETRY_BACKOFF_BASE ** attempt
            logger.warning(
                "Command failed (attempt %d/%d, exit code %d): %s "
                "— retrying in %ds...",
                attempt, max_retries, exc.returncode, cmd_str, wait_time
            )
            time.sleep(wait_time)


def is_pip_package_installed(package_name):
    """Checks if a package is installed via pip without raising an exception."""
    result = subprocess.run([sys.executable, '-m', 'pip', 'show', package_name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return result.returncode == 0


def find_nixl_wheel_in_cache(cache_dir):
    """Finds a nixl wheel file in the specified cache directory."""
    # The repaired wheel will have a 'manylinux' tag, but this glob still works.
    search_pattern = os.path.join(cache_dir, "nixl*.whl")
    wheels = glob.glob(search_pattern)
    if wheels:
        # Sort to get the most recent/highest version if multiple exist
        wheels.sort()
        return wheels[-1]
    return None


def install_system_dependencies(dry_run=False):
    """Installs required system packages using apt-get if run as root."""
    if os.geteuid() != 0:
        logger.warning("Not running as root. Skipping system dependency installation.")
        logger.warning(
            "Please ensure the following packages are installed on your system:\n"
            "  patchelf build-essential git cmake ninja-build "
            "autotools-dev automake meson libtool libtool-bin"
        )
        return

    logger.info("Running as root. Installing system dependencies...")
    apt_packages = [
        "patchelf",
        "build-essential",
        "git",
        "cmake",
        "ninja-build",
        "autotools-dev",
        "automake",
        "meson",
        "libtool",
        "libtool-bin"
    ]
    run_command(['apt-get', 'update'], dry_run=dry_run)
    run_command(['apt-get', 'install', '-y'] + apt_packages, dry_run=dry_run)
    logger.info("System dependencies installed successfully.")


def build_and_install_prerequisites(args):
    """Builds UCX and NIXL from source, creating a self-contained wheel."""

    dry_run = args.dry_run
    ucx_version = args.ucx_version
    nixl_version = args.nixl_version

    if not args.force_reinstall and is_pip_package_installed('nixl'):
        logger.info("NIXL is already installed. Nothing to do.")
        return

    cached_wheel = find_nixl_wheel_in_cache(WHEELS_CACHE_HOME)
    if not args.force_reinstall and cached_wheel:
        logger.info("Found self-contained wheel: %s.", os.path.basename(cached_wheel))
        logger.info("Installing from cache, skipping all source builds.")
        install_command = [sys.executable, '-m', 'pip', 'install', cached_wheel]
        run_command_with_retry(install_command, dry_run=dry_run)
        logger.info("Installation from cache complete.")
        return

    logger.info("No installed package or cached wheel found. Starting full build process...")

    logger.info("Installing auditwheel...")
    run_command_with_retry(
        [sys.executable, '-m', 'pip', 'install', 'auditwheel'], dry_run=dry_run
    )
    install_system_dependencies(dry_run=dry_run)
    ucx_install_path = os.path.abspath(UCX_INSTALL_DIR)
    logger.info("Using wheel cache directory: %s", WHEELS_CACHE_HOME)
    if not dry_run:
        os.makedirs(WHEELS_CACHE_HOME, exist_ok=True)

    # -- Step 1: Build UCX from source --
    logger.info("[1/3] Configuring and building UCX from source (version: %s)...", ucx_version)
    if not os.path.exists(UCX_DIR):
        run_command_with_retry(['git', 'clone', UCX_REPO_URL, UCX_DIR], dry_run=dry_run)
    ucx_source_path = os.path.abspath(UCX_DIR)
    run_command(['git', 'checkout', ucx_version], cwd=ucx_source_path, dry_run=dry_run)
    run_command(['./autogen.sh'], cwd=ucx_source_path, dry_run=dry_run)
    configure_command = [
        './configure',
        f'--prefix={ucx_install_path}',
        '--enable-shared',
        '--disable-static',
        '--disable-doxygen-doc',
        '--enable-optimizations',
        '--enable-cma',
        '--enable-devel-headers',
        '--with-verbs',
        '--enable-mt',
    ]
    run_command(configure_command, cwd=ucx_source_path, dry_run=dry_run)
    run_command(
        ['make', '-j', str(os.cpu_count() or 1)], cwd=ucx_source_path, dry_run=dry_run
    )
    run_command(['make', 'install'], cwd=ucx_source_path, dry_run=dry_run)
    logger.info("UCX build and install complete.")

    # -- Step 2: Build NIXL wheel from source --
    logger.info("[2/3] Building NIXL wheel from source (version: %s)...", nixl_version)
    if not os.path.exists(NIXL_DIR):
        run_command_with_retry(['git', 'clone', NIXL_REPO_URL, NIXL_DIR], dry_run=dry_run)

    run_command_with_retry(['git', 'fetch', '--all'], cwd=NIXL_DIR, dry_run=dry_run)
    nixl_tag = f'tags/{nixl_version}'
    nixl_branch = f'release-{nixl_version}'
    run_command(
        ['git', 'checkout', nixl_tag, '-b', nixl_branch], cwd=NIXL_DIR, dry_run=dry_run
    )

    build_env = os.environ.copy()
    build_env['PKG_CONFIG_PATH'] = os.path.join(ucx_install_path, 'lib', 'pkgconfig')
    ucx_lib_path = os.path.join(ucx_install_path, 'lib')
    ucx_plugin_path = os.path.join(ucx_lib_path, 'ucx')
    existing_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
    build_env['LD_LIBRARY_PATH'] = f"{ucx_lib_path}:{ucx_plugin_path}:{existing_ld_path}".strip(':')
    logger.debug("Using LD_LIBRARY_PATH: %s", build_env['LD_LIBRARY_PATH'])

    temp_wheel_dir = os.path.join(ROOT_DIR, 'temp_wheelhouse')
    run_command_with_retry(
        [sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps', f'--wheel-dir={temp_wheel_dir}'],
        cwd=os.path.abspath(NIXL_DIR),
        env=build_env,
        dry_run=dry_run,
    )

    # -- Step 3: Repair the wheel, excluding the already-bundled plugin --
    logger.info("[3/3] Repairing NIXL wheel to include UCX libraries...")
    if not dry_run:
        unrepaired_wheel = find_nixl_wheel_in_cache(temp_wheel_dir)
        if not unrepaired_wheel:
            raise RuntimeError("Failed to find the NIXL wheel after building it.")
    else:
        unrepaired_wheel = "<wheel-placeholder>"

    # Exclude the UCX plugin that mesonpy already bundles.
    auditwheel_command = [
        'auditwheel',
        'repair',
        '--exclude',
        'libplugin_UCX.so',
        unrepaired_wheel,
        f'--wheel-dir={WHEELS_CACHE_HOME}'
    ]
    run_command(auditwheel_command, env=build_env, dry_run=dry_run)

    # --- Cleanup ---
    run_command(['rm', '-rf', temp_wheel_dir], dry_run=dry_run)

    if not dry_run:
        newly_built_wheel = find_nixl_wheel_in_cache(WHEELS_CACHE_HOME)
        if not newly_built_wheel:
            raise RuntimeError("Failed to find the repaired NIXL wheel.")
    else:
        newly_built_wheel = "<wheel-placeholder>"

    logger.info(
        "Successfully built self-contained wheel: %s. Now installing...",
        os.path.basename(newly_built_wheel)
    )
    install_command = [sys.executable, '-m', 'pip', 'install', newly_built_wheel]
    if args.force_reinstall:
        install_command.insert(-1, '--force-reinstall')

    run_command_with_retry(install_command, dry_run=dry_run)
    logger.info("NIXL installation complete.")


def setup_logging(verbose=False):
    """Configure logging with a timestamped format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build and install UCX and NIXL dependencies."
    )
    parser.add_argument(
        '--force-reinstall',
        action='store_true',
        help='Force rebuild and reinstall even if already installed.',
    )
    parser.add_argument(
        '--ucx-version',
        default=DEFAULT_UCX_VERSION,
        help=f'UCX version/branch to checkout (default: {DEFAULT_UCX_VERSION}). '
             'Can also be set via UCX_VERSION env var.',
    )
    parser.add_argument(
        '--nixl-version',
        default=DEFAULT_NIXL_VERSION,
        help=f'NIXL version tag to checkout (default: {DEFAULT_NIXL_VERSION}). '
             'Can also be set via NIXL_VERSION env var.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Log all commands without executing them.',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose (DEBUG level) logging output.',
    )
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    build_and_install_prerequisites(args)
