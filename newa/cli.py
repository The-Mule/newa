import logging
import multiprocessing
import os.path
import time
from collections.abc import Iterable, Iterator
from functools import partial
from pathlib import Path

import click
from attrs import define

from . import (
    ErrataTool,
    ErratumConfig,
    ErratumJob,
    Event,
    EventType,
    ExecuteJob,
    Execution,
    InitialErratum,
    Issue,
    IssueHandler,
    IssueType,
    JiraJob,
    OnRespinAction,
    RawRecipeConfigDimension,
    Recipe,
    RecipeConfig,
    ScheduleJob,
    Settings,
    eval_test,
    render_template,
    )

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


@define
class CLIContext:
    """ State information about one Newa pipeline invocation """

    logger: logging.Logger
    settings: Settings

    # Path to directory with state files
    state_dirpath: Path

    def enter_command(self, command: str) -> None:
        self.logger.handlers[0].formatter = logging.Formatter(
            f'[%(asctime)s] [{command.ljust(8, " ")}] %(message)s',
            )

    def load_initial_erratum(self, filepath: Path) -> InitialErratum:
        erratum = InitialErratum.from_yaml_file(filepath)

        self.logger.info(f'Discovered initial erratum {erratum.event.id} in {filepath}')

        return erratum

    def load_initial_errata(self, filename_prefix: str) -> Iterator[InitialErratum]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_initial_erratum(self.state_dirpath / child)

    def load_erratum_job(self, filepath: Path) -> ErratumJob:
        job = ErratumJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered erratum job {job.id} in {filepath}')

        return job

    def load_erratum_jobs(self, filename_prefix: str) -> Iterator[ErratumJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_erratum_job(self.state_dirpath / child)

    def load_jira_job(self, filepath: Path) -> JiraJob:
        job = JiraJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered jira job {job.id} in {filepath}')

        return job

    def load_jira_jobs(self, filename_prefix: str) -> Iterator[JiraJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_jira_job(self.state_dirpath / child)

    def load_schedule_job(self, filepath: Path) -> ScheduleJob:
        job = ScheduleJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered schedule job {job.id} in {filepath}')

        return job

    def load_schedule_jobs(self, filename_prefix: str) -> Iterator[ScheduleJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_schedule_job(self.state_dirpath / child)

    def save_erratum_job(self, filename_prefix: str, job: ErratumJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Erratum job {job.id} written to {filepath}')

    def save_erratum_jobs(self, filename_prefix: str, jobs: Iterable[ErratumJob]) -> None:
        for job in jobs:
            self.save_erratum_job(filename_prefix, job)

    def save_jira_job(self, filename_prefix: str, job: JiraJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Jira job {job.id} written to {filepath}')

    def save_schedule_job(self, filename_prefix: str, job: ScheduleJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Schedule job {job.id} written to {filepath}')

    def save_execute_job(self, filename_prefix: str, job: ExecuteJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Execute job {job.id} written to {filepath}')


@click.group(chain=True)
@click.option(
    '--state-dir',
    default='$PWD/state',
    )
@click.option(
    '--conf-file',
    default='$HOME/.newa',
    )
@click.pass_context
def main(click_context: click.Context, state_dir: str, conf_file: str) -> None:
    ctx = CLIContext(
        settings=Settings.load(Path(os.path.expandvars(conf_file))),
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        )
    click_context.obj = ctx

    if not ctx.state_dirpath.exists():
        ctx.logger.info(f'State directory {ctx.state_dirpath} does not exist, creating...')
        ctx.state_dirpath.mkdir(parents=True)


@main.command(name='event')
@click.option(
    '-e', '--erratum', 'errata_ids',
    multiple=True,
    )
@click.pass_obj
def cmd_event(ctx: CLIContext, errata_ids: list[str]) -> None:
    ctx.enter_command('event')

    # Errata IDs were not given, try to load them from init- files.
    if not errata_ids:
        errata_ids = [e.event.id for e in ctx.load_initial_errata('init-')]

    # Abort if there are still no errata IDs.
    if not errata_ids:
        raise Exception('Missing errata IDs!')

    et_url = ctx.settings.et_url
    if not et_url:
        raise Exception('Errata Tool URL is not configured!')

    for erratum_id in errata_ids:
        event = Event(type_=EventType.ERRATUM, id=erratum_id)

        errata = ErrataTool(url=et_url).get_errata(event)

        for erratum in errata:
            erratum_job = ErratumJob(event=event, erratum=erratum)

            ctx.save_erratum_job('event-', erratum_job)


@main.command(name='jira')
@click.option(
    '--issue-config',
    default='component-config.yaml.sample',
    )
@click.pass_obj
def cmd_jira(ctx: CLIContext, issue_config: str) -> None:
    ctx.enter_command('jira')

    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')

    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira URL is not configured!')

    for erratum_job in ctx.load_erratum_jobs('event-'):

        # read Jira issue configuration
        config = ErratumConfig.from_yaml_file(Path(os.path.expandvars(issue_config)))

        jira = IssueHandler(erratum_job, jira_url, jira_token, config.project, config.transitions)
        ctx.logger.info(f"Initialized {jira}")

        # All issue action from the configuration.
        issue_actions = config.issues[:]

        # Processed action (action.id : issue).
        processed_actions: dict[str, Issue] = {}

        # Length of the queue the last time issue action was processed,
        # Use to prevent endless loop over the issue actions.
        endless_loop_check: dict[str, int] = {}

        # Iterate over issue actions. Take one, if it's not possible to finish it,
        # put it back at the end of the queue.
        while issue_actions:
            action = issue_actions.pop(0)

            ctx.logger.info(f"Processing {action.id}")

            if action.when and not eval_test(action.when,
                                             JOB=erratum_job,
                                             EVENT=erratum_job.event,
                                             ERRATUM=erratum_job.erratum):
                ctx.logger.info(f"Skipped, issue action is irrelevant ({action.when})")
                continue

            rendered_summary = render_template(action.summary, ERRATUM=erratum_job.erratum)
            rendered_description = render_template(action.description, ERRATUM=erratum_job.erratum)
            rendered_assignee = render_template(action.assignee, ERRATUM=erratum_job.erratum)

            # Detect that action has parent available (if applicable), if we went trough the
            # actions already and parent was not found, we abort.
            if action.parent_id and action.parent_id not in processed_actions:
                queue_length = len(issue_actions)
                last_queue_length = endless_loop_check.get(action.id, 0)
                if last_queue_length == queue_length:
                    raise Exception(f"Parent {action.parent_id} for {action.id} not found!")

                endless_loop_check[action.id] = queue_length
                ctx.logger.info(f"Skipped for now (parent {action.parent_id} not yet found)")

                issue_actions.append(action)
                continue

            # Find existing issues related to erratum_job and action
            search_result = jira.get_open_issues(action, all_respins=True)

            # Issues related to the curent respin and previous one(s).
            new_issues: list[Issue] = []
            old_issues: list[Issue] = []
            for jira_issue_key, jira_issue in search_result.items():
                ctx.logger.info(f"Checking {jira_issue_key}")

                # In general, issue is new (relevant to the current respin) if it has newa_id
                # of this action in the description. Otherwise, it is old (relevant to the
                # previous respins).
                #
                # However, it might happen that we encounter subtask issue that is new but its
                # original parent task got dropped (by human mistake, newa would never do that).
                # By this time new parent task already exists. Unfortunately, Jira REST API does
                # not allow updating 'parent' field [1] and hence we cannot re-use the issue with
                # updated parent - we need to handle it as an old one (unless it has KEEP on_respin
                # action it will get dropped and new one is created with the proper parent).
                #
                # [1] https://jira.atlassian.com/browse/JRASERVER-68763
                is_new = False
                if jira.newa_id(action) in jira_issue["description"] \
                        and (action.type != IssueType.SUBTASK
                             or not action.parent_id
                             or processed_actions[action.parent_id].id == jira_issue["parent"]):
                    is_new = True

                if is_new:
                    new_issues.append(Issue(jira_issue_key))
                else:
                    old_issues.append(Issue(jira_issue_key))

            # Old issue(s) can be re-used for the current respin.
            if old_issues and action.on_respin == OnRespinAction.KEEP:
                new_issues.extend(old_issues)
                old_issues = []

            # Processing new issues.
            #
            # 1. Either there is no new issue (it does not exist yet - we need to create it).
            if not new_issues:
                parent = None
                if action.parent_id:
                    parent = processed_actions.get(action.parent_id, None)

                issue = jira.create_issue(action,
                                          rendered_summary,
                                          rendered_description,
                                          rendered_assignee,
                                          parent)

                if action.job_recipe:
                    jira_job = JiraJob(event=erratum_job.event,
                                       erratum=erratum_job.erratum,
                                       jira=issue,
                                       recipe=Recipe(url=action.job_recipe))
                    ctx.save_jira_job('jira-', jira_job)

                processed_actions[action.id] = issue

                new_issues.append(issue)
                ctx.logger.info(f"New issue {issue.id} created")

            # Or there is exactly one new issue (already created or re-used old issue).
            elif len(new_issues) == 1:
                issue = new_issues[0]
                processed_actions[action.id] = issue

                # If the old issue was reused, re-fresh it.
                parent = processed_actions[action.parent_id] if action.parent_id else None
                jira.refresh_issue(action, issue)
                ctx.logger.info(f"Issue {issue} re-used")

            # But if there are more than one new issues we encountered error.
            else:
                raise Exception(f"More than one new {action.id} found ({new_issues})!")

            # Processing old issues - we only expect old issues that are to be closed (if any).
            if old_issues:
                if action.on_respin != OnRespinAction.CLOSE:
                    raise Exception(f"Invalid respin action {action.on_respin} for {old_issues}!")
                for issue in old_issues:
                    jira.drop_obsoleted_issue(issue, obsoleted_by=processed_actions[action.id])
                    ctx.logger.info(f"Old issue {issue} closed")


@main.command(name='schedule')
@click.pass_obj
def cmd_schedule(ctx: CLIContext) -> None:
    ctx.enter_command('schedule')

    for jira_job in ctx.load_jira_jobs('jira-'):
        # prepare parameters based on the recipe from recipe.url
        # generate all relevant test request using the recipe data
        # prepare a list of Request objects

        # identify compose to be used
        # just a dump conversion for now
        compose = jira_job.erratum.release.rstrip('.GA') + '-Nightly'
        initial_config = RawRecipeConfigDimension(compose=compose)

        config = RecipeConfig.from_yaml_url(jira_job.recipe.url)
        # build requests
        requests = list(config.build_requests(initial_config))
        ctx.logger.info(f'{len(requests)} requests have been generated')

        # create few fake Issue objects for now
        for request in requests:
            schedule_job = ScheduleJob(
                event=jira_job.event,
                erratum=jira_job.erratum,
                jira=jira_job.jira,
                recipe=jira_job.recipe,
                request=request)
            ctx.save_schedule_job('schedule-', schedule_job)


@main.command(name='execute')
@click.option(
    '--workers',
    default=4,
    )
@click.pass_obj
def cmd_execute(ctx: CLIContext, workers: int) -> None:
    ctx.enter_command('execute')

    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    # make TESTING_FARM_API_TOKEN available to workers as envvar if it has been
    # defined only though the settings file
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token

    # get a list of files to be scheduled so that they can be distributed across workers
    schedule_list = [
        (ctx, ctx.state_dirpath / child.name)
        for child in ctx.state_dirpath.iterdir()
        if child.name.startswith('schedule-')]

    worker_pool = multiprocessing.Pool(workers)
    for _ in worker_pool.starmap(worker, schedule_list):
        # small sleep to avoid race conditions inside tmt code
        time.sleep(0.1)

    print('Done')


def worker(ctx: CLIContext, schedule_file: Path) -> None:

    log = partial(print, schedule_file.name)

    log('processing request...')
    # read request details
    schedule_job = ScheduleJob.from_yaml_file(Path(schedule_file))
    log('initiating TF request')
    tf_request = schedule_job.request.initiate_tf_request()
    # tf_request = TFRequest(
    #    api='https://api.dev.testing-farm.io/v0.1/requests/519f5c01-46b6-47c9-a055-aecaa32e6a20',
    #    uuid='519f5c01-46b6-47c9-a055-aecaa32e6a20')
    log(f'TF request filed with uuid {tf_request.uuid}')
    finished = False
    delay = int(ctx.settings.tf_recheck_delay)
    while not finished:
        time.sleep(delay)
        tf_request.fetch_details()
        state = tf_request.details['state']
        log(f'TF reqest {tf_request.uuid} state: {state}')
        finished = state in ['complete', 'error']
    execute_job = ExecuteJob(
        event=schedule_job.event,
        erratum=schedule_job.erratum,
        jira=schedule_job.jira,
        recipe=schedule_job.recipe,
        request=schedule_job.request,
        execution=Execution(return_code=0, artifacts_url=tf_request.details['run']['artifacts']),
        )
    execute_job.to_yaml_file(
        schedule_file.parent /
        schedule_file.name.replace(
            'schedule-',
            'execute-'))
    log(f'finished with result: {tf_request.details["result"]["overall"]}')


@main.command(name='report')
@click.pass_obj
def cmd_report(ctx: CLIContext) -> None:
    ctx.enter_command('report')

    for _ in ctx.load_erratum_jobs('execute-'):
        pass
        # read yaml details
        # update Jira issue with job result
