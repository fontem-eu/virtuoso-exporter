"""Declare what this image copies out of the Virtuoso image, for its SBOM.

isql and the libraries it links (all but glibc) are copied from Virtuoso's image
rather than installed by a package manager, so no package database here
lists them. Their identities come from that image itself: Virtuoso's version
from its own declaration, the libraries' Ubuntu package versions from its
dpkg database. docker-build-sign adds the result to the SBOM and requires
every executable file to be covered.

Usage: sbom-declare.py <virtuoso declared.json> <virtuoso dpkg status> <virtuoso os-release>
"""
import json
import sys

LIB = "/opt/virtuoso-opensource/lib"
COPIED = {  # path in this image -> the Ubuntu package it comes from
    f"{LIB}/libssl.so.3": "libssl3t64",
    f"{LIB}/libcrypto.so.3": "libssl3t64",
    "/usr/lib/x86_64-linux-gnu/ossl-modules/legacy.so": "libssl3t64",
    f"{LIB}/libedit.so.2": "libedit2",
    f"{LIB}/libtinfo.so.6": "libtinfo6",
    f"{LIB}/libbsd.so.0": "libbsd0",
    f"{LIB}/libmd.so.0": "libmd0",
}


def dpkg_versions(path):
    versions, pkg = {}, {}
    for line in open(path).read().splitlines() + [""]:
        if not line:
            if pkg.get("Package"):
                versions[pkg["Package"]] = (pkg.get("Version"), pkg.get("Architecture"))
            pkg = {}
        elif ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            pkg[k] = v.strip()
    return versions


def main(declared, status, os_release):
    virtuoso = next(c for c in json.load(open(declared))["components"] if c["name"] == "virtuoso-opensource")
    osr = dict(l.split("=", 1) for l in open(os_release).read().splitlines() if "=" in l)
    distro = f"{osr['ID'].strip(chr(34))}-{osr['VERSION_ID'].strip(chr(34))}"
    have = dpkg_versions(status)
    comps = [dict(virtuoso, paths=["/opt/virtuoso-opensource/bin/isql"])]
    by_package = {}
    for path, package in COPIED.items():
        by_package.setdefault(package, []).append(path)
    for package, paths in by_package.items():
        if package not in have:
            sys.exit(f"{package} (for {', '.join(paths)}) is not in the Virtuoso image's dpkg database")
        version, arch = have[package]
        comps.append({"name": package, "version": version, "type": "library",
                      "purl": f"pkg:deb/ubuntu/{package}@{version}?arch={arch}&distro={distro}",
                      "paths": paths})
    json.dump({"components": comps}, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main(*sys.argv[1:4])
