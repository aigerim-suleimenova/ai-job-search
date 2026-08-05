"""Which shared libraries a Linux build must not carry inside: the system's own.

PyInstaller copies into the build every library the Python extensions were linked
against, and on Linux that drags in the whole desktop stack the build machine
happened to have. At run time the loader looks in the build's folder first, so
those copies shadow the machine's own.

WebKit, which actually draws the window, is not in the build: it is taken from
the system, and it brings its own dependencies with it. That is where the mix
comes apart. On a machine newer than the build machine the system's libsecret
asked the build's older GLib for `g_variant_builder_init_static`, did not get it,
WebKit refused to load — and the program, left without a window, opened itself in
the browser instead.

So the desktop stack is taken from the system, whole. Nothing is lost by that: a
window needs the system's GTK and WebKit anyway, and a build made on Ubuntu 24.04
will not start on anything older regardless — its libc sets the floor, and every
distribution above that floor carries these libraries.

Not on the list on purpose: libgirepository, libffi, libz, libssl, libsqlite3 and
their like. Those are ours, not the desktop's — the system's GTK does not reach
for them, so a copy inside the build shadows nothing and saves us from a machine
that happens to lack one.
"""

# Beginnings of names rather than whole ones: sonames carry versions, and the
# versions change under us.
SYSTEM_STACK = (
    # GLib — this is exactly where it broke.
    "libglib-2.0", "libgobject-2.0", "libgio-2.0", "libgmodule-2.0", "libgthread-2.0",
    # …and what GLib itself stands on: leaving these behind would recreate the
    # same mismatch one floor down.
    "libpcre2-", "libmount", "libblkid", "libselinux",
    # GTK and what it draws with
    "libgtk-3", "libgtk-4", "libgdk-3", "libgdk_pixbuf-2.0",
    "libatk-1.0", "libatk-bridge-2.0", "libatspi",
    "libcairo", "libpixman-1", "libpng16",
    "libpango-1.0", "libpangocairo-1.0", "libpangoft2-1.0",
    "libharfbuzz", "libfribidi", "libthai", "libdatrie", "libgraphite2",
    "libfontconfig", "libfreetype", "libbrotli",
    "libepoxy", "libxkbcommon", "libwayland-",
    # X11, underneath all of it
    "libX11", "libXau", "libXdmcp", "libXext", "libXrender", "libXi",
    "libXfixes", "libXcursor", "libXinerama", "libXrandr", "libXcomposite",
    "libXdamage", "libxcb",
    # WebKit itself and its neighbours — should a future build ever collect them
    "libwebkit2gtk", "libjavascriptcoregtk", "libsoup-", "libsecret-1",
    "libgcrypt", "libgpg-error",
)


def is_system_library(path: str) -> bool:
    """Does this file belong to the desktop stack — must it come from the system?

    Takes either a bare name or a path: PyInstaller lists some libraries inside
    folders of their own.
    """
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if ".so" not in name:
        return False                    # not a shared library — none of our business
    return name.startswith(SYSTEM_STACK)
