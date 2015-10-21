from __future__ import absolute_import

import csv
from email.parser import FeedParser
import logging
import sys
import StringIO
import warnings

from pip.basecommand import Command
from pip.exceptions import DistributionNotFound
from pip.index import FormatControl, fmt_ctl_formats, PackageFinder, Search
from pip.req import InstallRequirement
from pip.utils import (
    get_installed_distributions, dist_is_editable, canonicalize_name)
from pip.utils.deprecation import RemovedInPip10Warning
from pip.wheel import WheelCache
from pip.cmdoptions import make_option_group, index_group


logger = logging.getLogger(__name__)


class ListCommand(Command):
    """
    List installed packages, including editables.

    Packages are listed in a case-insensitive sorted order.
    """
    name = 'list'
    usage = """
      %prog [options]"""
    summary = 'List installed packages.'

    def __init__(self, *args, **kw):
        super(ListCommand, self).__init__(*args, **kw)

        cmd_opts = self.cmd_opts

        cmd_opts.add_option(
            '-o', '--outdated',
            action='store_true',
            default=False,
            help='List outdated packages (excluding editables)')
        cmd_opts.add_option(
            '-u', '--uptodate',
            action='store_true',
            default=False,
            help='List uptodate packages (excluding editables)')
        cmd_opts.add_option(
            '-e', '--editable',
            action='store_true',
            default=False,
            help='List editable projects.')
        cmd_opts.add_option(
            '-l', '--local',
            action='store_true',
            default=False,
            help=('If in a virtualenv that has global access, do not list '
                  'globally-installed packages.'),
        )
        self.cmd_opts.add_option(
            '--user',
            dest='user',
            action='store_true',
            default=False,
            help='Only output packages installed in user-site.')

        cmd_opts.add_option(
            '--pre',
            action='store_true',
            default=False,
            help=("Include pre-release and development versions. By default, "
                  "pip only finds stable versions."),
        )
        cmd_opts.add_option(
            '--csv',
            dest='csv',
            action='store_true',
            default=False,
            help='Output csv formatted fields.')

        index_opts = make_option_group(index_group, self.parser)

        self.parser.insert_option_group(0, index_opts)
        self.parser.insert_option_group(0, cmd_opts)

    def _build_package_finder(self, options, index_urls, session):
        """
        Create a package finder appropriate to this list command.
        """
        return PackageFinder(
            find_links=options.find_links,
            index_urls=index_urls,
            allow_all_prereleases=options.pre,
            trusted_hosts=options.trusted_hosts,
            process_dependency_links=options.process_dependency_links,
            session=session,
        )

    def run(self, options, args):
        if options.allow_external:
            warnings.warn(
                "--allow-external has been deprecated and will be removed in "
                "the future. Due to changes in the repository protocol, it no "
                "longer has any effect.",
                RemovedInPip10Warning,
            )

        if options.allow_all_external:
            warnings.warn(
                "--allow-all-external has been deprecated and will be removed "
                "in the future. Due to changes in the repository protocol, it "
                "no longer has any effect.",
                RemovedInPip10Warning,
            )

        if options.allow_unverified:
            warnings.warn(
                "--allow-unverified has been deprecated and will be removed "
                "in the future. Due to changes in the repository protocol, it "
                "no longer has any effect.",
                RemovedInPip10Warning,
            )

        if options.outdated:
            self.run_outdated(options)
        elif options.uptodate:
            self.run_uptodate(options)
        elif options.editable:
            self.run_editables(options)
        else:
            self.run_listing(options)

    def run_outdated(self, options):
        for dist, version, typ in self.find_packages_latest_versions(options):
            if version > dist.parsed_version:
                logger.info(
                    '%s (Current: %s Latest: %s [%s])',
                    dist.project_name, dist.version, version, typ,
                )

    def find_packages_latest_versions(self, options):
        index_urls = [options.index_url] + options.extra_index_urls
        if options.no_index:
            logger.info('Ignoring indexes: %s', ','.join(index_urls))
            index_urls = []

        dependency_links = []
        for dist in get_installed_distributions(local_only=options.local,
                                                user_only=options.user):
            if dist.has_metadata('dependency_links.txt'):
                dependency_links.extend(
                    dist.get_metadata_lines('dependency_links.txt'),
                )

        with self._build_session(options) as session:
            finder = self._build_package_finder(options, index_urls, session)
            finder.add_dependency_links(dependency_links)

            installed_packages = get_installed_distributions(
                local_only=options.local,
                user_only=options.user,
                include_editables=False,
            )
            format_control = FormatControl(set(), set())
            wheel_cache = WheelCache(options.cache_dir, format_control)
            for dist in installed_packages:
                req = InstallRequirement.from_line(
                    dist.key, None, isolated=options.isolated_mode,
                    wheel_cache=wheel_cache
                )
                typ = 'unknown'
                try:
                    link = finder.find_requirement(req, True)

                    # If link is None, means installed version is most
                    # up-to-date
                    if link is None:
                        continue
                except DistributionNotFound:
                    continue
                else:
                    canonical_name = canonicalize_name(req.name)
                    formats = fmt_ctl_formats(format_control, canonical_name)
                    search = Search(
                        req.name,
                        canonical_name,
                        formats)
                    remote_version = finder._link_package_versions(
                        link, search).version
                    if link.is_wheel:
                        typ = 'wheel'
                    else:
                        typ = 'sdist'
                yield dist, remote_version, typ

    def run_listing(self, options):
        installed_packages = get_installed_distributions(
            local_only=options.local,
            user_only=options.user,
        )
        self.output_package_listing(options, installed_packages)

    def run_editables(self, options):
        installed_packages = get_installed_distributions(
            local_only=options.local,
            user_only=options.user,
            editables_only=True,
        )
        self.output_package_listing(options, installed_packages)

    def output_package_listing(self, options, installed_packages):
        installed_packages = sorted(
            installed_packages,
            key=lambda dist: dist.project_name.lower(),
        )
        if options.csv:
            logger.info('project_name,version,license,summary,author,homepage')
        for dist in installed_packages:
            if options.csv:
                line_io = StringIO.StringIO()
                writer = csv.writer(line_io)
                fields = [dist.project_name, dist.version, '', '', '', '']
                if dist.has_metadata('PKG-INFO'):
                    feed_parser = FeedParser()
                    feed_parser.feed(dist.get_metadata('PKG-INFO'))
                    pkg_info_dict = feed_parser.close()
                    fields[2] = pkg_info_dict.get('License')
                    fields[3] = pkg_info_dict.get('Summary')
                    fields[4] = pkg_info_dict.get('Author')
                    fields[5] = pkg_info_dict.get('Home-page')
                writer.writerow(fields)
                line = line_io.getvalue().strip()
            elif dist_is_editable(dist):
                line = '%s (%s, %s)' % (
                    dist.project_name,
                    dist.version,
                    dist.location,
                )
            else:
                line = '%s (%s)' % (dist.project_name, dist.version)
            if line:
                logger.info(line)

    def run_uptodate(self, options):
        uptodate = []
        for dist, version, typ in self.find_packages_latest_versions(options):
            if dist.parsed_version == version:
                uptodate.append(dist)
        self.output_package_listing(options, uptodate)
