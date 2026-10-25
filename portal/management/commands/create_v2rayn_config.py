import json
import sqlite3
import shutil
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand

from portal.models import Device
from portal.config_generator import (
    generate_client_config,
    ConfigGenerationError,
)


class Command(BaseCommand):
    help = "Create Karion v2rayN Windows package"


    def add_arguments(self, parser):
        parser.add_argument(
            "token",
            type=str,
        )


    def handle(self, *args, **options):

        token = options["token"]

        try:
            device = Device.objects.get(
                token=token,
            )
        except Device.DoesNotExist:
            self.stderr.write(
                self.style.ERROR(
                    "device not found"
                )
            )
            return


        if not device.remnawave_uuid:
            self.stderr.write(
                self.style.ERROR(
                    "device has no Remnawave UUID"
                )
            )
            return


        base_dir = Path("/tmp")

        package_name = (
            f"Karion-{device.name}"
        )

        out_dir = (
            base_dir /
            package_name
        )


        if out_dir.exists():
            shutil.rmtree(out_dir)


        template = Path(
            "/opt/karion-portal/windows-template"
        )


        if not template.exists():
            self.stderr.write(
                self.style.ERROR(
                    "template not found"
                )
            )
            return


        shutil.copytree(
            template,
            out_dir,
        )


        config_dir = (
            out_dir /
            "guiconfigs"
        )

        config_dir.mkdir(
            parents=True,
            exist_ok=True,
        )


        db_file = (
            config_dir /
            "guiNDB.db"
        )


        #
        # Удаляем БД из шаблона.
        # Создаём новую с двумя профилями.
        #
        if db_file.exists():
            db_file.unlink()


        try:

            selective_config = (
                generate_client_config(
                    device,
                    "selective",
                )
            )

            full_config = (
                generate_client_config(
                    device,
                    "full",
                )
            )

        except ConfigGenerationError as exc:

            self.stderr.write(
                self.style.ERROR(
                    f"config generation error: {exc}"
                )
            )

            return


        self.create_database(
            db_file=db_file,
            config_dir=config_dir,
            selective_config=selective_config,
            full_config=full_config,
            device_token=device.token,
        )


        archive_path = (
            base_dir /
            package_name
        )

        old_archive = Path(
            str(archive_path) + ".zip"
        )

        if old_archive.exists():
            old_archive.unlink()


        archive = shutil.make_archive(
            str(archive_path),
            "zip",
            base_dir,
            package_name,
        )


        self.stdout.write(
            self.style.SUCCESS(
                "DONE"
            )
        )

        self.stdout.write(
            f"folder: {out_dir}"
        )

        self.stdout.write(
            f"archive: {archive}"
        )


    def create_database(
        self,
        db_file,
        config_dir,
        selective_config,
        full_config,
        device_token,
    ):

        db = sqlite3.connect(
            db_file
        )


        db.execute("""
        CREATE TABLE SubItem (
            Id varchar primary key not null,
            Remarks varchar,
            Url varchar,
            MoreUrl varchar,
            Enabled integer,
            UserAgent varchar,
            Sort integer,
            Filter varchar,
            AutoUpdateInterval integer,
            UpdateTime integer,
            ConvertTarget varchar,
            PrevProfile varchar,
            NextProfile varchar,
            PreSocksPort integer,
            Memo varchar,
            CustomCoreType integer
        )
        """)


        db.execute("""
        CREATE TABLE ProfileItem (
            IndexId varchar primary key not null,
            ConfigType integer,
            CoreType integer,
            ConfigVersion integer,
            Subid varchar,
            IsSub integer,
            PreSocksPort integer,
            DisplayLog integer,
            Remarks varchar,
            Address varchar,
            Port integer,
            Password varchar,
            Username varchar,
            Network varchar,
            HeaderType varchar,
            RequestHost varchar,
            Path varchar,
            StreamSecurity varchar,
            AllowInsecure varchar,
            Sni varchar,
            Alpn varchar,
            Fingerprint varchar,
            PublicKey varchar,
            ShortId varchar,
            SpiderX varchar,
            Mldsa65Verify varchar,
            Extra varchar,
            MuxEnabled integer,
            Cert varchar,
            CertSha varchar,
            EchConfigList varchar,
            VerifyPeerCertByName varchar,
            Finalmask varchar,
            ProtoExtra varchar,
            TransportExtra varchar,
            Ports varchar,
            AlterId integer,
            Flow varchar,
            Id varchar,
            Security varchar
        )
        """)


        #
        # Создаём два настоящих Xray JSON.
        #

        profiles = (
            (
                "Karion Selective",
                selective_config,
            ),
            (
                "Karion Full",
                full_config,
            ),
        )


        subscription_urls = {
            "Karion Selective": (
                f"https://lk.karion24.ru/device/{device_token}/selective.json"
            ),
            "Karion Full": (
                f"https://lk.karion24.ru/device/{device_token}/full.json"
            ),
        }

        subscription_ids = {}

        for sort_order, (name, url) in enumerate(
            subscription_urls.items(), start=1
        ):
            sub_id = uuid.uuid4().hex
            subscription_ids[name] = sub_id
            db.execute(
                """
                INSERT INTO SubItem (
                    Id, Remarks, Url, MoreUrl, Enabled,
                    UserAgent, Sort, Filter, AutoUpdateInterval,
                    UpdateTime, ConvertTarget, PrevProfile,
                    NextProfile, PreSocksPort, Memo, CustomCoreType
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sub_id, name, url, "", 1,
                    "", sort_order, None, 60,
                    0, None, None, None, None, None, 2,
                ),
            )

        for sort_order, (
            profile_name,
            profile_config,
        ) in enumerate(
            profiles,
            start=1,
        ):

            profile_id = str(
                uuid.uuid4()
            )

            profile_file = (
                config_dir /
                f"{profile_id}.json"
            )

            profile_file.write_text(
                json.dumps(
                    profile_config,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )


            db.execute(
                """
                INSERT INTO ProfileItem VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    str(
                        int(
                            uuid.uuid4().int
                            % 10**19
                        )
                    ),
                    2,
                    2,
                    4,
                    subscription_ids[profile_name],
                    1,
                    None,
                    1,
                    profile_name,
                    f"{profile_id}.json",
                    0,
                    "",
                    "",
                    "",
                    None,
                    None,
                    None,
                    "",
                    "",
                    None,
                    "",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "{}",
                    None,
                    None,
                    0,
                    None,
                    None,
                    None,
                    None,
                )
            )


        db.commit()
        db.close()
