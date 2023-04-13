##
# Copyright 2009-2023 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/easybuilders/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
EasyBuild support for installing Cargo packages (Rust lang package system)

@author: Mikael Oehman (Chalmers University of Technology)
"""

import os

import easybuild.tools.environment as env
from easybuild.tools.build_log import EasyBuildError
from easybuild.framework.easyconfig import CUSTOM
from easybuild.framework.easyblock import EasyBlock
from easybuild.tools.filetools import extract_file, change_dir
from easybuild.tools.run import run_cmd
from easybuild.tools.config import build_option
from easybuild.tools.filetools import write_file, compute_checksum

CRATESIO_SOURCE = "https://crates.io/api/v1/crates"


class Cargo(EasyBlock):
    """Support for installing Cargo packages (Rust)"""

    @staticmethod
    def extra_options(extra_vars=None):
        """Define extra easyconfig parameters specific to Cargo"""
        extra_vars = EasyBlock.extra_options(extra_vars)
        extra_vars.update({
            'enable_tests': [True, "Enable building of tests", CUSTOM],
            'offline': [True, "Build offline", CUSTOM],
            'lto': [None, "Override default LTO flag ('fat', 'thin', 'off')", CUSTOM],
            'crates': [[], "List of (crate, version) tuples to use", CUSTOM],
        })

        return extra_vars

    def __init__(self, *args, **kwargs):
        """Constructor for Cargo easyblock."""
        super(Cargo, self).__init__(*args, **kwargs)
        env.setvar('CARGO_HOME', os.path.join(self.builddir, '.cargo'))
        env.setvar('RUSTC', 'rustc')
        env.setvar('RUSTDOC', 'rustdoc')
        env.setvar('RUSTFMT', 'rustfmt')
        optarch = build_option('optarch')
        if not optarch:
            optarch = 'native'
        env.setvar('RUSTFLAGS', '-C target-cpu=%s' % optarch)
        env.setvar('RUST_LOG', 'DEBUG')
        env.setvar('RUST_BACKTRACE', '1')

        # Populate sources from "crates" list of tuples
        sources = self.cfg['sources']
        for crate, version in self.cfg['crates']:
            sources.append({
                'download_filename': crate + '/' + version + '/download',
                'filename': crate + '-' + version + '.tar.gz',
                'source_urls': [CRATESIO_SOURCE],
                'alt_location': 'crates.io',
            })
        self.cfg.update('sources', sources)

    def configure_step(self):
        pass

    def extract_step(self):
        """
        Unpack the source files and populate them with required .cargo-checksum.json if offline
        """
        for src in self.src:
            existing_dirs = set(os.listdir(self.builddir))
            self.log.info("Unpacking source %s" % src['name'])
            srcdir = extract_file(src['path'], self.builddir, cmd=src['cmd'],
                                  extra_options=self.cfg['unpack_options'], change_into_dir=False)
            change_dir(srcdir)
            if srcdir:
                self.src[self.src.index(src)]['finalpath'] = srcdir
            else:
                raise EasyBuildError("Unpacking source %s failed", src['name'])

            # Create checksum file for all sources required by vendored crates.io sources
            new_dirs = set(os.listdir(self.builddir)) - existing_dirs
            if self.cfg['offline'] and len(new_dirs) == 1:
                cratedir = new_dirs.pop()
                self.log.info('creating .cargo-checksums.json file for : %s', cratedir)
                chksum = compute_checksum(src['path'], checksum_type='sha256')
                chkfile = os.path.join(self.builddir, cratedir, '.cargo-checksum.json')
                write_file(chkfile, '{"files":{},"package":"%s"}' % chksum)

    @property
    def profile(self):
        return 'debug' if self.toolchain.options.get('debug', None) else 'release'

    def build_step(self):
        """Build with cargo"""
        parallel = ''
        if self.cfg['parallel']:
            parallel = "-j %s" % self.cfg['parallel']

        tests = ''
        if self.cfg['enable_tests']:
            tests = "--tests"

        offline = ''
        if self.cfg['offline']:
            offline = "--offline"
            # Replace crates-io with vendored sources
            write_file('.cargo/config.toml', '[source.crates-io]\ndirectory=".."', append=True)

        lto = ''
        if self.cfg['lto'] is not None:
            lto = '--config profile.%s.lto=\\"%s\\"' % (self.profile, self.cfg['lto'])

        run_cmd('rustc --print cfg', log_all=True, simple=True)  # for tracking in log file
        cmd = ' '.join([
            self.cfg['prebuildopts'],
            'cargo build',
            '--profile=' + self.profile,
            offline,
            lto,
            tests,
            parallel,
            self.cfg['buildopts'],
        ])
        run_cmd(cmd, log_all=True, simple=True)

    def test_step(self):
        """Test with cargo"""
        if self.cfg['enable_tests']:
            offline = ''
            if self.cfg['offline']:
                offline = "--offline"

            cmd = ' '.join([
                self.cfg['pretestopts'],
                'cargo test',
                '--profile=' + self.profile,
                offline,
                self.cfg['testopts'],
            ])
            run_cmd(cmd, log_all=True, simple=True)

    def install_step(self):
        """Install with cargo"""
        offline = ''
        if self.cfg['offline']:
            offline = "--offline"

        cmd = ' '.join([
            self.cfg['preinstallopts'],
            'cargo install',
            '--profile=' + self.profile,
            offline,
            '--root=' + self.installdir,
            '--path=.',
            self.cfg['installopts'],
        ])
        run_cmd(cmd, log_all=True, simple=True)


def generate_crate_list(sourcedir):
    """Helper for generating crate list"""
    import toml

    cargo_toml = toml.load(os.path.join(sourcedir, 'Cargo.toml'))
    cargo_lock = toml.load(os.path.join(sourcedir, 'Cargo.lock'))

    app_name = cargo_toml['package']['name']
    deps = cargo_lock['package']

    app_in_cratesio = False
    crates = []
    other_crates = []
    for dep in deps:
        name = dep['name']
        version = dep['version']
        if 'source' in dep and dep['source'] == 'registry+https://github.com/rust-lang/crates.io-index':
            if name == app_name:
                app_in_cratesio = True  # exclude app itself, needs to be first in crates list
            else:
                crates.append((name, version))
        else:
            other_crates.append((name, version))
    return app_in_cratesio, crates, other_crates


if __name__ == '__main__':
    import sys
    app_in_cratesio, crates, _ = generate_crate_list(sys.argv[1])
    if app_in_cratesio or crates:
        print('crates = [')
        if app_in_cratesio:
            print('    (name, version),')
        for name, version in crates:
            print("    ('" + name + "', '" + version + "'),")
        print(']')

