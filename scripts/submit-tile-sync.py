#!/usr/bin/env python


import time
from pathlib import Path
from subprocess import check_output
from typing import Mapping

import click

SUBMIT_THROTTLE_SECS = 1


@click.command()
@click.argument('job_name')
@click.argument('tile_folder', type=click.Path(exists=True, readable=True, writable=False))
@click.option('--submit-limit', type=int, default=None, help="Max number of jobs to submit (remaining tiles will "
                                                             "not be submitted)")
@click.option('--concurrent-jobs', type=int, default=5, help="Number of PBS jobs to run concurrently")
def main(job_name: str, tile_folder: Path, submit_limit: int, concurrent_jobs: int):
    tile_path = Path(tile_folder).absolute()

    run_directory = Path('runs').absolute()

    # For input tile_path, get list of unique tile X values
    # They are named "X_Y", eg "-12_23"
    tile_xs = set(int(p.name.split('_')[0]) for p in tile_path.iterdir() if p.name != 'ncml')
    tile_xs = sorted(tile_xs)

    print("Found %s total jobs" % len(tile_xs))

    submitted = 0

    # To maintain concurrent_jobs limit, we set a pbs dependency on previous jobs.

    # mapping of slot number to the last job id to be submitted in it.
    # type: Mapping[int, str]
    last_job_slots = {}

    for i, tile_x in enumerate(tile_xs):
        if submitted == submit_limit:
            print("Submit limit ({}) reached, done.".format(submit_limit))
            break

        subjob_name = '{}_{}'.format(job_name, tile_x)

        output_path = run_directory.joinpath('{}.tsv'.format(subjob_name))
        error_path = run_directory.joinpath('{}.log'.format(subjob_name))

        if output_path.exists():
            print("[{}] {}: output exists, skipping".format(i, subjob_name))
            continue

        last_job_id = last_job_slots.get(submitted % concurrent_jobs)

        job_id = submit_job(
            error_path=error_path,
            # Folders are named "X_Y", we glob for all folders with the give X coord.
            input_folders=list(tile_path.glob('{}_*'.format(tile_x))),
            output_path=output_path,
            subjob_name=subjob_name,
            require_job_id=last_job_id
        )
        print("[{}] {}: submitted {}".format(i, subjob_name, job_id))
        last_job_slots[submitted % concurrent_jobs] = job_id
        submitted += 1

        time.sleep(SUBMIT_THROTTLE_SECS)


def submit_job(error_path,
               input_folders,
               output_path,
               subjob_name,
               require_job_id=None,
               sync_workers=4,
               verbose=True,
               dry_run=True):
    requirements = []
    sync_opts = []
    if require_job_id:
        requirements.extend(['-W', 'depend=afterok:{}'.format(str(require_job_id).strip())])
    if verbose:
        sync_opts.append('-v')
    if not dry_run:
        # For tile products like the current FC we trust the index over the filesystem.
        # (jobs that failed part-way-through left datasets on disk and were not indexed)
        sync_opts.extend(['--trash-missing', '--trash-archived'])
        # Scene products are the opposite:
        # Only complete scenes are written to fs, so '--index-missing' instead of trash.
        # (also want to '--update-locations' to fix any moved datasets)

    sync_command = [
        'python', '-m', 'datacubenci.sync',
        '-j', str(sync_workers),
        *sync_opts,
        *(map(str, input_folders))
    ]
    command = [
        'qsub', '-V',
        '-P', 'v10',
        '-q', 'express',
        '-l', 'walltime=20:00:00,mem=4GB,ncpus=2,jobfs=1GB,other=gdata',
        '-l', 'wd',
        '-N', 'sync-{}'.format(subjob_name),
        '-m', 'e',
        '-M', 'jeremy.hooke@ga.gov.au',
        '-e', str(error_path),
        '-o', str(output_path),
        *requirements,
        '--',
        *sync_command
    ]
    print(' '.join(command))
    output = check_output(command)
    job_id = output.decode('utf-8').strip(' \\n')
    return job_id


if __name__ == '__main__':
    # Eg. scripts/submit-tile-sync.py 5fc /g/data/fk4/datacube/002/LS5_TM_FC
    main()