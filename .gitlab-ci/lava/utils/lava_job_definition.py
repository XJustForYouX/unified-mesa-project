# How many attempts should be made when a timeout happen during LAVA device boot.
from os import getenv
from typing import Any

NUMBER_OF_ATTEMPTS_LAVA_BOOT = int(getenv("LAVA_NUMBER_OF_ATTEMPTS_LAVA_BOOT", 3))

# Supports any integers in [0, 100].
# The scheduler considers the job priority when ordering the queue
# to consider which job should run next.
JOB_PRIORITY = int(getenv("LAVA_JOB_PRIORITY", 75))


def generate_lava_yaml_payload(args) -> dict[str, Any]:
    # General metadata and permissions, plus also inexplicably kernel arguments
    values = {
        "job_name": "mesa: {}".format(args.pipeline_info),
        "device_type": args.device_type,
        "visibility": {"group": [args.visibility_group]},
        "priority": JOB_PRIORITY,
        "context": {
            "extra_nfsroot_args": " init=/init rootwait usbcore.quirks=0bda:8153:k"
        },
        "timeouts": {
            "job": {"minutes": args.job_timeout_min},
            "actions": {
                "depthcharge-retry": {
                    # Could take between 1 and 1.5 min in slower boots
                    "minutes": 2
                },
                "depthcharge-start": {
                    # Should take less than 1 min.
                    "minutes": 1,
                },
                "depthcharge-action": {
                    # This timeout englobes the entire depthcharge timing,
                    # including retries
                    "minutes": 2
                    * NUMBER_OF_ATTEMPTS_LAVA_BOOT,
                },
            },
        },
    }

    if args.lava_tags:
        values["tags"] = args.lava_tags.split(",")

    # URLs to our kernel rootfs to boot from, both generated by the base
    # container build
    deploy = {
        "timeout": {"minutes": 10},
        "to": "tftp",
        "os": "oe",
        "kernel": {
            "url": "{}/{}".format(args.kernel_url_prefix, args.kernel_image_name),
        },
        "nfsrootfs": {
            "url": "{}/lava-rootfs.tar.zst".format(args.rootfs_url_prefix),
            "compression": "zstd",
        },
    }
    if args.kernel_image_type:
        deploy["kernel"]["type"] = args.kernel_image_type
    if args.dtb_filename:
        deploy["dtb"] = {
            "url": "{}/{}.dtb".format(args.kernel_url_prefix, args.dtb_filename)
        }

    # always boot over NFS
    boot = {
        "failure_retry": NUMBER_OF_ATTEMPTS_LAVA_BOOT,
        "method": args.boot_method,
        "commands": "nfs",
        "prompts": ["lava-shell:"],
    }

    # skeleton test definition: only declaring each job as a single 'test'
    # since LAVA's test parsing is not useful to us
    run_steps = []
    test = {
        "timeout": {"minutes": args.job_timeout_min},
        "failure_retry": 1,
        "definitions": [
            {
                "name": "mesa",
                "from": "inline",
                "lava-signal": "kmsg",
                "path": "inline/mesa.yaml",
                "repository": {
                    "metadata": {
                        "name": "mesa",
                        "description": "Mesa test plan",
                        "os": ["oe"],
                        "scope": ["functional"],
                        "format": "Lava-Test Test Definition 1.0",
                    },
                    "run": {"steps": run_steps},
                },
            }
        ],
    }

    # job execution script:
    #   - inline .gitlab-ci/common/init-stage1.sh
    #   - fetch and unpack per-pipeline build artifacts from build job
    #   - fetch and unpack per-job environment from lava-submit.sh
    #   - exec .gitlab-ci/common/init-stage2.sh

    with open(args.first_stage_init, "r") as init_sh:
        run_steps += [
            x.rstrip() for x in init_sh if not x.startswith("#") and x.rstrip()
        ]
        run_steps.append(
            f"curl -L --retry 4 -f --retry-all-errors --retry-delay 60 {args.job_rootfs_overlay_url} | tar -xz -C /",
        )

    if args.jwt_file:
        with open(args.jwt_file) as jwt_file:
            run_steps += [
                "set +x",
                f'echo -n "{jwt_file.read()}" > "{args.jwt_file}"  # HIDEME',
                "set -x",
                f'echo "export CI_JOB_JWT_FILE={args.jwt_file}" >> /set-job-env-vars.sh',
            ]
    else:
        run_steps += [
            "echo Could not find jwt file, disabling MINIO requests...",
            "sed -i '/MINIO_RESULTS_UPLOAD/d' /set-job-env-vars.sh",
        ]

    run_steps += [
        "mkdir -p {}".format(args.ci_project_dir),
        "curl {} | tar --zstd -x -C {}".format(args.build_url, args.ci_project_dir),
        # Sleep a bit to give time for bash to dump shell xtrace messages into
        # console which may cause interleaving with LAVA_SIGNAL_STARTTC in some
        # devices like a618.
        "sleep 1",
        # Putting CI_JOB name as the testcase name, it may help LAVA farm
        # maintainers with monitoring
        f"lava-test-case 'mesa-ci_{args.mesa_job_name}' --shell /init-stage2.sh",
    ]

    values["actions"] = [
        {"deploy": deploy},
        {"boot": boot},
        {"test": test},
    ]

    return values