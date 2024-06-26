"""Command line interface for the toolkit. This module provides a command line interface for the toolkit. It is used to run experiments, manage trackers and datasets, and to perform other tasks."""

import os
import sys
import argparse
import logging
import yaml
from datetime import datetime

from .. import check_updates, toolkit_version, get_logger, check_debug
from . import Progress, normalize_path

logger = get_logger()

class EnvDefault(argparse.Action):
    """Argparse action that resorts to a value in a specified envvar if no value is provided via program arguments.
    """
    def __init__(self, envvar, required=True, default=None, separator=None, **kwargs):
        """Initialize the action"""
        if not default and envvar:
            if envvar in os.environ:
                default = os.environ[envvar]
        if separator:
            default = default.split(separator)
        if required and default:
            required = False
        self.separator = separator
        super(EnvDefault, self).__init__(default=default, required=required,
                                         **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        """Call the action"""
        if self.separator:
            values = values.split(self.separator)
        setattr(namespace, self.dest, values)

def do_test(config: argparse.Namespace):
    """Run a test for a tracker

    Args:
        config (argparse.Namespace): Configuration
    """
    from vot.dataset.dummy import generate_dummy
    from vot.dataset import load_sequence, Frame
    from vot.tracker import ObjectStatus, Registry, TrackerException
    from vot.experiment.helpers import MultiObjectHelper
    from vot.dataset.proxy import ObjectsHideFilterSequence
    from vot import config as global_config

    trackers = Registry(global_config.registry)

    if not config.tracker:
        logger.error("Unable to continue without a tracker")
        logger.error("List of found trackers: ")
        for k in trackers.identifiers():
            logger.error(" * %s", k)
        return

    if not config.tracker in trackers:
        logger.error("Tracker does not exist")
        return

    tracker = trackers[config.tracker]

    def visualize(axes, frame: Frame, reference, state):
        """Visualize the frame and the state of the tracker. 
        
        Args:
            axes (matplotlib.axes.Axes): The axes to draw on.
            frame (Frame): The frame to draw.
            reference (list): List of references.
            state (ObjectStatus): The state of the tracker.
            
        """
        axes.clear()
        handle.image(frame.channel())
        if not isinstance(state, list):
            state = [state]
        for gt, st in zip(reference, state):
            handle.style(color="green").region(gt)
            handle.style(color="red").region(st.region)
        
    try:

        runtime = tracker.runtime(log=True)

        logger.info("Generating dummy sequence")

        if config.sequence is None:
            sequence = generate_dummy(50, objects=3 if runtime.multiobject else 1)
        else:
            sequence = load_sequence(normalize_path(config.sequence))

        if config.ignore:
            sequence = ObjectsHideFilterSequence(sequence, config.ignore)

        logger.info("Obtaining runtime for tracker %s", tracker.identifier)

        context = {"continue" : True}

        def on_press(event):
            """Callback for key press event.
            
            Args:
                event (matplotlib.backend_bases.Event): The event.
            """
            if event.key == 'q':
                context["continue"] = False

        if config.visualize:
            import matplotlib.pylab as plt
            from vot.utilities.draw import MatplotlibDrawHandle
            figure = plt.figure()
            if hasattr(figure.canvas, "set_window_title"):
                figure.canvas.set_window_title('VOT Test')
            axes = figure.add_subplot(1, 1, 1)
            axes.set_aspect("equal")
            handle = MatplotlibDrawHandle(axes, size=sequence.size)
            context["click"] = figure.canvas.mpl_connect('key_press_event', on_press)
            handle.style(fill=False)
            figure.show()
            

        helper = MultiObjectHelper(sequence)

        logger.info("Initializing tracker")

        frame = sequence.frame(0)
        state, _ = runtime.initialize(frame, [ObjectStatus(frame.object(x), {}) for x in helper.new(0)])

        if config.visualize:
            visualize(axes, frame, [frame.object(x) for x in helper.objects(0)], state)
            figure.canvas.draw()

        for i in range(1, len(sequence)):
            
            logger.info("Processing frame %d/%d", i, len(sequence)-1)
            frame = sequence.frame(i)
            state, _ = runtime.update(frame, [ObjectStatus(frame.object(x), {}) for x in helper.new(i)])

            if config.visualize:
                visualize(axes, frame, [frame.object(x) for x in helper.objects(i)], state)
                figure.canvas.draw()
                figure.canvas.flush_events()
                figure.savefig(f'/data1/zhangjiaming/Mamba/WORK_SPACE_LIANGCHENG/MixFormer_Mamba/vot24/test_imgs/{i}.png')

            if not context["continue"]:
                break

        logger.info("Stopping tracker")

        runtime.stop()

        logger.info("Test concluded successfuly")

    except TrackerException as te:
        logger.error("Error during tracker execution: {}".format(te))
        if runtime:
            runtime.stop()
    except KeyboardInterrupt:
        if runtime:
            runtime.stop()

def do_initialize(config: argparse.Namespace):
    """Initialize a workspace. If a stack is provided, the workspace is initialized with the stack. If no stack is provided,
    but a dataset exists, then a dummy config can be created for this custom dataset. If neither is provided, the user is prompted to
    provide a stack.

    Args:
        config (argparse.Namespace): Configuration
    """

    from vot.workspace import WorkspaceException, Workspace
    from ..stack import resolve_stack, list_integrated_stacks

    if Workspace.exists(config.workspace):
        logger.error("Workspace already initialized")
        return

    if config.stack is None:
        if os.path.isfile(os.path.join(config.workspace, "configuration.m")):
            from vot.utilities.migration import migrate_matlab_workspace
            migrate_matlab_workspace(config.workspace)
            return
        elif os.path.isfile(os.path.join(config.workspace, "sequences")):
            sequences_directory = os.path.join(config.workspace, "sequences")
            # Attempt to load a dataset from the sequences directory
            from vot.dataset import load_dataset
            logger.info("Found sequences directory, attempting to load dataset")
            try:
                dataset = load_dataset(sequences_directory)
                logger.info("Loaded dataset: %s", dataset)

            except Exception as e:
                pass
            if dataset is not None:
                logger.info("Loaded dataset: %s", dataset)
                default_config = dict(dataset=dataset)
                Workspace.initialize(config.workspace, default_config, download=False)
                logger.info("Initialized workspace in '%s'", config.workspace)
                return

        else:
            stacks = list_integrated_stacks()
            logger.error("Unable to continue without a stack")
            logger.error("List of available integrated stacks: ")
            for k, v in sorted(stacks.items(), key=lambda x: x[0]):
                logger.error(" * %s - %s", k, v)

            return

    stack_file = resolve_stack(config.stack)

    if stack_file is None:
        logger.error("Experiment stack %s not found", stack_file)
        return

    default_config = dict(stack=config.stack, registry=["./trackers.ini"])

    try:
        Workspace.initialize(config.workspace, default_config, download=not config.nodownload)
        logger.info("Initialized workspace in '%s'", config.workspace)
    except WorkspaceException as we:
        logger.error("Error during workspace initialization: %s", we)

def do_evaluate(config: argparse.Namespace):
    """Run an evaluation for a tracker on an experiment stack and a set of sequences.
    
    Args:
        config (argparse.Namespace): Configuration    
    """

    from vot.experiment import run_experiment
    from ..tracker import TrackerException
    from ..workspace import Workspace

    workspace = Workspace.load(config.workspace)

    logger.debug("Loaded workspace in '%s'", config.workspace)

    trackers = workspace.registry.resolve(*config.trackers, storage=workspace.storage.substorage("results"), skip_unknown=False)

    if len(trackers) == 0:
        logger.error("Unable to continue without at least on tracker")
        logger.error("List of available found trackers: ")
        for k in workspace.registry.identifiers():
            logger.error(" * %s", k)
        return

    # Filter experiments
    if config.experiments:
        experiments = [v for k, v in workspace.stack.experiments.items() if k in config.experiments.split(",")]
    else:
        experiments = workspace.stack

    if len(experiments) == 0:
        logger.error("No experiments found, stopping.")
        return

    try:
        for tracker in trackers:
            logger.debug("Evaluating tracker %s", tracker.identifier)
            for experiment in experiments:
                run_experiment(experiment, tracker, workspace.dataset, config.force, config.persist)

        logger.info("Evaluation concluded successfuly")

    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by the user")
    except TrackerException as te:
        logger.error("Evaluation interrupted by tracker error: {}".format(te))

def do_analysis(args: argparse.Namespace):
    """Run an analysis for a tracker on an experiment stack and a set of sequences. Analysis results are serialized
    to disk either as a JSON file or as a YAML file.

    Args:
        args (argparse.Namespace): Configuration
    """
    from vot import config

    from vot.analysis import AnalysisProcessor, process_stack_analyses
    from vot.report import generate_serialized
    from ..workspace import Workspace
    from ..workspace.storage import Cache

    workspace = Workspace.load(args.workspace)

    logger.debug("Loaded workspace in '%s'", args.workspace)

    if not args.trackers:
        trackers = workspace.list_results(workspace.registry)
    else:
        trackers = workspace.registry.resolve(*args.trackers, storage=workspace.storage.substorage("results"), skip_unknown=False)

    if not trackers:
        logger.warning("No trackers resolved, stopping.")
        return

    logger.debug("Running analysis for %d trackers", len(trackers))

    if config.worker_pool_size == 1:

        if args.debug:
            from vot.analysis.processor import DebugExecutor
            logging.getLogger("concurrent.futures").setLevel(logging.DEBUG)
            executor = DebugExecutor()
        else:
            from vot.utilities import ThreadPoolExecutor
            executor = ThreadPoolExecutor(1)

    else:
        from concurrent.futures import ProcessPoolExecutor
        executor = ProcessPoolExecutor(config.worker_pool_size)

    if not config.persistent_cache:
        from cachetools import LRUCache
        cache = LRUCache(1000)
    else:
        cache = Cache(workspace.storage.substorage("cache").substorage("analysis"))

    try:

        with AnalysisProcessor(executor, cache):

            results = process_stack_analyses(workspace, trackers)

            if results is None:
                return

            if args.name is None:
                name = "{:%Y-%m-%dT%H-%M-%S.%f%z}".format(datetime.now())
            else:
                name = args.name

            storage = workspace.storage.substorage("analysis")

            if args.format == "json":
                generate_serialized(trackers, workspace.dataset, results, storage, "json", name)
            elif args.format == "yaml":
                generate_serialized(trackers, workspace.dataset, results, storage, "yaml", name)
            else:
                raise ValueError("Unknown format '{}'".format(args.format))

            logger.info("Analysis successful, report available as %s", name)

    finally:

        executor.shutdown(wait=True)

def do_report(config: argparse.Namespace):
    """Generate a report for a one or multiple trackers on an experiment stack and a set of sequences.

    Args:
        config (argparse.Namespace): Configuration
    """

    from vot.report import generate_document
    from ..workspace import Workspace


    if config.name is None:
        name = "{:%Y-%m-%dT%H-%M-%S.%f%z}".format(datetime.now())
    else:
        name = config.name

    workspace = Workspace.load(config.workspace)

    logger.debug("Loaded workspace in '%s'", config.workspace)

    if not config.trackers:
        trackers = workspace.list_results(workspace.registry)
    else:
        trackers = workspace.registry.resolve(*config.trackers, storage=workspace.storage.substorage("results"), skip_unknown=False)

    if not trackers:
        logger.warning("No trackers resolved, stopping.")
        return

    logger.debug("Running report generation for %d trackers", len(trackers))

    generate_document(workspace, trackers, config.format, name, config.sequences, config.experiments)
    
    logger.info("Report generation successful, document available as %s", name)
    
    
def do_pack(config: argparse.Namespace):
    """Package results to a ZIP file so that they can be submitted to a challenge.

    Args:
        config (argparse.Namespace): Configuration
    """

    import zipfile, io
    from shutil import copyfileobj
    from ..workspace import Workspace
    from vot.utilities.io import YAMLEncoder

    workspace = Workspace.load(config.workspace)

    logger.debug("Loaded workspace in '%s'", config.workspace)

    tracker = workspace.registry[config.tracker]

    logger.info("Packaging results for tracker %s", tracker.identifier)

    all_files = []
    can_finish = True

    with Progress("Scanning", len(workspace.dataset) * len(workspace.stack)) as progress:

        for experiment in workspace.stack:
            sequences = experiment.transform(workspace.dataset)
            for sequence in sequences:
                complete, files, results = experiment.scan(tracker, sequence)
                all_files.extend([(f, experiment.identifier, sequence.name, results) for f in files])
                if not complete:
                    logger.error("Results are not complete for experiment %s, sequence %s", experiment.identifier, sequence.name)
                    can_finish = False
                progress.relative(1)

    if not can_finish:
        logger.error("Unable to continue, experiments not complete")
        return

    logger.debug("Collected %d files, compressing to archive ...", len(all_files))

    timestamp = datetime.now()

    archive_name = "{}_{:%Y-%m-%dT%H-%M-%S.%f%z}.zip".format(tracker.identifier, timestamp)

    with Progress("Compressing", len(all_files)) as progress:

        manifest = dict(identifier=tracker.identifier, configuration=tracker.describe(),
            timestamp="{:%Y-%m-%dT%H-%M-%S.%f%z}".format(timestamp), platform=sys.platform,
            python=sys.version, toolkit=toolkit_version(), stack=workspace.dump()["stack"])

        with zipfile.ZipFile(workspace.storage.write(archive_name, binary=True), mode="w") as archive:
            for f in all_files:
                info = zipfile.ZipInfo(filename=os.path.join(f[1], f[2], f[0]), date_time=timestamp.timetuple())
                with archive.open(info, mode="w") as fout, f[3].read(f[0]) as fin:
                    if isinstance(fin, io.TextIOBase):
                        copyfileobj(fin, io.TextIOWrapper(fout))
                    else:
                        copyfileobj(fin, fout)
                progress.relative(1)

            info = zipfile.ZipInfo(filename="manifest.yml", date_time=timestamp.timetuple())
            with io.TextIOWrapper(archive.open(info, mode="w")) as fout:
                yaml.dump(manifest, fout, Dumper=YAMLEncoder)

    logger.info("Result packaging successful, archive available in %s", archive_name)

def main():
    """Entrypoint to the toolkit Command Line Interface utility, should be executed as a program and provided with arguments.
    """

    parser = argparse.ArgumentParser(description='VOT Toolkit Command Line Interface', prog="vot")
    parser.add_argument("--debug", "-d", default=False, help="Backup backend", required=False, action='store_true')
    parser.add_argument("--registry", default=".", help='Tracker registry paths', required=False)

    subparsers = parser.add_subparsers(help='commands', dest='action', title="Commands")

    test_parser = subparsers.add_parser('test', help='Test a tracker integration on a synthetic sequence')
    test_parser.add_argument("tracker", help='Tracker identifier', nargs="?")
    test_parser.add_argument("--visualize", "-g", default=False, required=False, help='Visualize results of the test session', action='store_true')
    test_parser.add_argument("--sequence", "-s", required=False, help='Path to sequence to use instead of dummy')
    test_parser.add_argument("--ignore", required=False, help='Object IDs to ignore', type=lambda x: x.split(","), default=[])

    workspace_parser = subparsers.add_parser('configure', aliases=["initialize"], help='Setup a new workspace and download data')
    workspace_parser.add_argument("--workspace", default=os.getcwd(), help='Workspace path')
    workspace_parser.add_argument("--nodownload", default=False, required=False, help="Do not download dataset if specified in stack", action='store_true')
    workspace_parser.add_argument("stack", nargs="?", help='Experiment stack')

    evaluate_parser = subparsers.add_parser('evaluate', aliases=["run"], help='Evaluate one or more trackers in a given workspace')
    evaluate_parser.add_argument("trackers", nargs='+', default=None, help='Tracker identifiers')
    evaluate_parser.add_argument("--force", "-f", default=False, help="Force rerun of the entire evaluation", required=False, action='store_true')
    evaluate_parser.add_argument("--persist", "-p", default=False, help="Persist execution even in case of an error", required=False, action='store_true')
    evaluate_parser.add_argument("--workspace", default=os.getcwd(), help='Workspace path')
    evaluate_parser.add_argument("--experiments", default=None, help='Filter specified experiments (comma separated names)', required=False)

    analysis_parser = subparsers.add_parser('analysis', aliases=["analyse", "analyze"], help='Run analysis of results')
    analysis_parser.add_argument("trackers", nargs='*', help='Tracker identifiers')
    analysis_parser.add_argument("--workspace", default=os.getcwd(), help='Workspace path')
    analysis_parser.add_argument("--format", choices=("json", "yaml"), default="json", help='Analysis output format')
    analysis_parser.add_argument("--name", required=False, help='Analysis output name')

    report_parser = subparsers.add_parser('report', aliases=["document"], help='Generate report document')
    report_parser.add_argument("trackers", nargs='*', help='Tracker identifiers')
    report_parser.add_argument("--workspace", default=os.getcwd(), help='Workspace path')
    report_parser.add_argument("--format", choices=("html", "latex", "plots"), default="html", help='Analysis output format')
    report_parser.add_argument("--name", required=False, help='Document output name')
    report_parser.add_argument("--sequences", default=None, help='Filter specified sequences (comma separated names)', required=False)
    report_parser.add_argument("--experiments", default=None, help='Filter specified experiments (comma separated names)', required=False)

    pack_parser = subparsers.add_parser('pack', help='Package results for submission')
    pack_parser.add_argument("--workspace", default=os.getcwd(), help='Workspace path')
    pack_parser.add_argument("tracker", help='Tracker identifier')

    from vot import print_config

    try:

        args = parser.parse_args()

        if args.registry:
            os.environ["VOT_REGISTRY"] = os.pathsep.join(os.environ.get("VOT_REGISTRY", "").split(os.pathsep) + [args.registry])

        if args.debug:
            os.environ["VOT_DEBUG_MODE"] = "1"
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

        print_config()
        
        def check_version():
            """Check if a newer version of the toolkit is available."""
            update, version = check_updates()
            if update:
                logger.warning("A newer version of the VOT toolkit is available (%s), please update.", version)

        if args.action == "test":
            check_version()
            do_test(args)
        elif args.action in ["configure", "initialize"]:
            check_version()
            do_initialize(args)
        elif args.action in ["evaluate", "run"]:
            check_version()
            do_evaluate(args)
        elif args.action in ["analysis", "analyse", "analyze"]:
            check_version()
            do_analysis(args)
        elif args.action in ["report", "document"]:
            check_version()
            do_report(args)
        elif args.action == "pack":
            check_version()
            do_pack(args)
        else:
            parser.print_help()

    except argparse.ArgumentError as e:
        logger.error(e)
        exit(-1)
    except Exception as e:
        logger.exception(e, exc_info=check_debug())
        exit(1)

    exit(0)
