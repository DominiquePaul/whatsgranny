from datetime import datetime

from supabase import Client, create_client

import grannymail.config as cfg
import grannymail.db.classes as dbc
from grannymail.utils.utils import get_message_spreadsheet


class NoEntryFoundError(Exception):
    """An error raised when no entry was found for retrieval or deletion and it might go unnoticed if not raised. Deletion is probably the best example."""

    def __init__(self, table: str, key: str, data: str | float | int) -> None:
        self.message = (
            f"No entry found in table {table} for searching for {key} = {data}"
        )
        super().__init__(self.message)


class DuplicateEntryError(Exception):
    def __init__(self, message="Duplicate entry already exists in the database"):
        self.message = message
        super().__init__(self.message)


class SupabaseClient:
    def __init__(
        self,
        url: str = cfg.SUPABASE_URL,
        key: str = cfg.SUPABASE_KEY,
        bucket=cfg.SUPABASE_BUCKET_NAME,
    ):
        """Instantiates an object to query the sql database.

        This is implemented as a class such that the same options can be called for a similar
        class that connects to a different sql database provider

        Args:
            url (str, optional): URL of the supabase sql database. Defaults
                to os.environ.get("SUPABASE_URL").
            key (str, optional): secret API key (not the anon key) Key must be private.
                Defaults to os.environ.get("SUPABASE_KEY").
        """
        self.client: Client = create_client(url, key)
        self.bucket = bucket
        self.obj_to_table = {
            dbc.User: "users",
            dbc.Message: "messages",
            dbc.File: "files",
            dbc.Address: "addresses",
            dbc.Draft: "drafts",
            dbc.Order: "orders",
            dbc.Attachment: "attachments",
            dbc.Changelog: "changelog",
        }

    def _check_duplicates(self, table: str, data: dbc.AbstractDataTableClass):
        """Checks for a set of column values whether they already exist in the database

        Args:
            table (str): the DB table to search through
            values (dict): the values to search for. For convenience the entire set of data
                that should be added to the table can be passed here.
            keys (list): the keys of the values dict that should actually checked for existence.
                The values in the list are expected to be found as keys in the values dict and as
                    columns in the table.

        Returns:
            list: the keys/columns with the values already in the table
        """
        duplicated_values = []
        data_dict = data.to_dict()
        for key in data._unique_fields:
            if key in data_dict.keys():
                response = (
                    self.client.table(table)
                    .select("*")
                    .eq(key, data_dict[key])
                    .execute()
                )
                if response.data != []:
                    duplicated_values.append(key)
        if duplicated_values:
            raise DuplicateEntryError()

    def _validate_exactly_one_supabase_item(
        self,
        response: list,
        table: str,
        field_name: str,
        field_value: str | float | int,
    ) -> None:
        """Checks that the response from Supabase is valid.

        This means that a single entry was found with the given field_name and field_value

        Args:
            response (list): _description_
            field_name (str): _description_
            field_value (str | float | int): _description_

        Raises:
            ValueError: If the response is not a list. For example if there is an error in the query
            NoEntryFoundError: No entry found in the database for searching for {key} = {data}
            ValueError: Multiple entries found with {key} = {data}
        """
        if not isinstance(response, list):
            raise ValueError(
                f"Response from Supabase was not a list. Instead got {type(response)}"
            )
        if len(response) != 1:
            if len(response) == 0:
                raise NoEntryFoundError(table, field_name, field_value)
            else:
                raise ValueError(
                    f"More than one user found with {field_name} {field_value}"
                )

    def _get_obj_info(self, table: str, obj: dbc.AbstractDataTableClass) -> dict:
        """Completes the information of an object from the database by searching for its unique values"""
        if not isinstance(obj, dbc.AbstractDataTableClass):
            raise ValueError(
                f"Expected an object of type dbc.AbstractDataTableClass. Instead got {type(obj)}"
            )
        unique_fields = [
            field for field in obj._unique_fields if getattr(obj, field) is not None
        ]
        if len(unique_fields) == 0:
            raise ValueError(
                f"Object of type {type(obj)} does not have any unique fields that can used to search for an entry"
            )
        else:
            unique_field = unique_fields[0]
            response = (
                self.client.table(table)
                .select("*")
                .eq(unique_field, getattr(obj, unique_field))
                .execute()
                .data
            )
            self._validate_exactly_one_supabase_item(
                response, table, unique_field, getattr(obj, unique_field)
            )
            return response[0]

    def _delete_entry(
        self, obj: dbc.AbstractDataTableClass, deletion_key: str | None = None
    ) -> int:
        """Completes the information of an object from the database by searching for its unique values

        Args:
            obj (AbstractDataTableClass): The object to delete
            deletion_key (str, optional): The key to use for deletion. Defaults to None. If None, the first unique key of the object is used

        Returns:
            int: the number of items deleted. Throws an error if no item is deleted

        """
        if not isinstance(obj, dbc.AbstractDataTableClass):
            raise ValueError(
                f"Expected an object of type dbc.AbstractDataTableClass. Instead got {type(obj)}"
            )
        table = self.obj_to_table[type(obj)]
        if deletion_key is None:
            deletion_key = obj._unique_fields[0]
            if getattr(obj, deletion_key) is None:
                raise ValueError(
                    f"Object of type {type(obj)} does not have a value for {deletion_key} that can be "
                    + "used for deletion and no alternative deletion key was provided using 'deletion_key'"
                )
        response = (
            self.client.table(table)
            .delete()
            .eq(deletion_key, getattr(obj, deletion_key))
            .execute()
        )
        items_deleted = len(response.data)
        if items_deleted == 0:
            raise NoEntryFoundError(table, deletion_key, getattr(obj, deletion_key))
        else:
            return items_deleted

    def get_user(self, data: dbc.User) -> dbc.User:
        """Completes dbc.User information by retrieving full record from the database

        Args:
            data (User): An object of type dbc.User. The object must have at
            least one value that is unique in the database and that can be
            used to find the record.

        Returns:
            dbc.User: The user augmented with all the information from the database
        """
        return dbc.User(**self._get_obj_info("users", data))

    def add_user(self, user: dbc.User) -> dbc.User:
        """Adds a user to the database

        Args:
            user (User): information about the user to be added. The user must have at least one of the following fields: email, phone_number, telegram_id
        """
        duplicates = self._check_duplicates("users", user)
        if duplicates:
            raise DuplicateEntryError()
        else:
            r = self.client.table("users").insert(user.to_dict()).execute()
            return dbc.User(**r.data[0])

    def add_changelog(self, changelog: dbc.Changelog) -> tuple[int, str]:
        r = self.client.table("changelog").insert(changelog.to_dict()).execute()
        return 0, "Changelog added successfully"

    def update_user(
        self, user_data: dbc.User, user_update: dbc.User
    ) -> tuple[int, str]:
        """Updates a user in the database

        Args:
            user_data (User): The user data to update. The user data must contain the user_id
        """
        user_data_full = self.get_user(user_data)
        fields_updated = user_data_full.find_different_fields(user_update)
        if not fields_updated:
            return 1, "No fields were updated"
        else:
            timestamp_utc = str(datetime.utcnow())
            for field in fields_updated:
                changelog = dbc.Changelog(
                    timestamp=timestamp_utc,
                    table_name="users",
                    row_id=user_data_full.user_id,
                    column_name=field,
                    column_type=type(getattr(user_update, field)).__name__,
                    old_value=getattr(user_data_full, field),
                    new_value=getattr(user_update, field),
                )
                self.add_changelog(changelog)

        self.client.table("users").update(user_update.to_dict()).eq(
            "user_id", user_data_full.user_id
        ).execute()
        return 0, "User updated successfully"

    def delete_user(self, user: dbc.User) -> tuple[int, str]:
        """Deletes a user from the database

        Args:
            user (User): The user data to delete. The user data must contain the user_id
        """
        user_full = self.get_user(user)
        self.client.table("users").delete().eq("user_id", user_full.user_id).execute()
        return 0, "User deleted successfully"

    def get_message(self, message: dbc.Message) -> dbc.Message:
        """Completes dbc.User information by retrieving full record from the database

        Args:
            data (Message): An object of type dbc.Message. The object must have at
            least one value that is unique in the database and that can be
            used to find the record.

        Returns:
            dbc.User: The dbc.Message data augmented with all the information from the database
        """
        return dbc.Message(**self._get_obj_info("messages", message))

    def get_all_user_messages(self, user: dbc.User) -> list[dbc.Message]:
        user = self.get_user(user)
        response = (
            self.client.table("messages")
            .select("*")
            .eq("user_id", user.user_id)
            .order("timestamp", desc=False)
            .execute()
        )
        data = response.data
        message_list: list[dbc.Message] = [dbc.Message(**message) for message in data]
        return message_list

    def add_message(self, msg_data: dbc.Message) -> dbc.Message:
        duplicates = self._check_duplicates("messages", msg_data)
        if duplicates:
            raise DuplicateEntryError()
        else:
            r = self.client.table("messages").insert(msg_data.to_dict()).execute()
        incoming_type = type(msg_data)
        filtered_data = {k: v for k, v in r.data[0].items() if v is not None}
        return incoming_type(**filtered_data)

    def update_message(
        self, msg_data: dbc.Message, msg_update: dbc.Message
    ) -> tuple[int, str]:
        """Updates a message in the database

        Args:
            msg_data (Message): The message data to update. The message data must contain the message_id
        """
        msg_data_full = self.get_message(msg_data)
        self.client.table("messages").update(msg_update.to_dict()).eq(
            "message_id", msg_data_full.message_id
        ).execute()
        return 0, "Message updated successfully"

    def get_file(self, data: dbc.File) -> dbc.File:
        """Completes dbc.User information by retrieving full record from the database

        Args:
            data (Message): An object of type dbc.Message. The object must have at
            least one value that is unique in the database and that can be
            used to find the record.

        Returns:
            dbc.User: The dbc.Message data augmented with all the information from the database
        """
        return dbc.File(**self._get_obj_info("files", data))

    def add_file(self, file: dbc.File) -> dbc.File:
        duplicates = self._check_duplicates("files", file)
        if duplicates:
            raise DuplicateEntryError()
        response = self.client.table("files").insert(file.to_dict()).execute()
        assert len(response.data) == 1
        file_data = response.data[0]
        return dbc.File(**file_data)

    def get_user_addresses(self, user: dbc.User) -> list[dbc.Address]:
        user = self.get_user(user)
        if user.user_id is None:
            raise ValueError("User does not have a user_id. Cannot retrieve addresses")
        response = (
            self.client.table("addresses")
            .select("*")
            .eq("user_id", user.user_id)
            .order("created_at", desc=False)
            .execute()
        ).data
        address_list: list[dbc.Address] = [
            dbc.Address(**address) for address in response
        ]
        return address_list

    def add_address(self, address: dbc.Address) -> dbc.Address:
        if address.user_id is None:
            raise ValueError("Address does not have a user_id. Cannot add address")
        response = self.client.table("addresses").insert(address.to_dict()).execute()
        full_address = dbc.Address(**response.data[0])
        return full_address

    def delete_address(self, address: dbc.Address) -> tuple[int, str]:
        if address.address_id is None:
            raise ValueError(
                "Address does not have a address_id. Cannot delete address"
            )
        (
            self.client.table("addresses")
            .delete()
            .eq("address_id", address.address_id)
            .execute()
        )
        return 0, "Address deleted successfully"

    def get_user_drafts(self, user: dbc.User) -> list[dbc.Draft]:
        user = self.get_user(user)
        if user.user_id is None:
            raise ValueError("User does not have a user_id. Cannot retrieve addresses")
        response = (
            self.client.table("drafts")
            .select("*")
            .eq("user_id", user.user_id)
            .order("created_at", desc=False)
            .execute()
        )
        data = response.data
        draft_list: list[dbc.Draft] = [dbc.Draft(**draft) for draft in data]
        return draft_list

    def get_last_draft(self, user: dbc.User) -> dbc.Draft | None:
        drafts = self.get_user_drafts(user)
        if len(drafts) == 0:
            return None
        else:
            return drafts[-1]

    def add_draft(self, draft: dbc.Draft) -> dbc.Draft:
        response = self.client.table("drafts").insert(draft.to_dict()).execute()
        assert len(response.data) == 1
        draft_data = response.data[0]
        return dbc.Draft(**draft_data)

    def add_attachment(self, attachment: dbc.Attachment) -> dbc.Attachment:
        response = (
            self.client.table("attachments").insert(attachment.to_dict()).execute()
        )
        assert len(response.data) == 1
        attachment_data = response.data[0]
        return dbc.Attachment(**attachment_data)

    # ---------------

    def upload_file(self, filebytes: bytes, user_id: str, mime_type: str) -> str:
        if mime_type == "audio/ogg":
            suffix = ".ogg"
        elif mime_type == "application/pdf":
            suffix = ".pdf"
        else:
            raise ValueError(f"mime_type {mime_type} not supported for file upload")
        # create file name based on user_id and timestamp
        bucket_path = (
            f"memos/{user_id}/{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}{suffix}"
        )
        # upload to supabase storage
        self.client.storage.from_(self.bucket).upload(
            file=filebytes, path=bucket_path, file_options={"content-type": mime_type}
        )

        return bucket_path

    def download_draft(self, draft: dbc.Draft) -> bytes:
        if draft.blob_path is None:
            raise ValueError("Draft does not have a blob_path. Cannot download draft")
        return self.client.storage.from_(self.bucket).download(draft.blob_path)

    def register_voice_message(self, filebytes: bytes, message: dbc.Message):
        if message.user_id is None:
            raise ValueError(
                "Message does not have a user_id. Cannot register voice memo"
            )
        # upload to supabase storage
        mime_type = "audio/ogg"
        bucket_path = self.upload_file(filebytes, message.user_id, mime_type=mime_type)
        file = dbc.File(
            message_id=message.message_id, mime_type=mime_type, blob_path=bucket_path
        )
        self.add_file(file)

    def register_message(
        self,
        user: dbc.User,
        sent_by: str,
        attachment_mime_type: str | None,
        message_body: str | None,
        command: str | None,
        transcript: str | None,
    ) -> dbc.Message:
        """Registers a message in the database

        Args:
            telegram_id (str): The telegram_id of the user that sent the message
            sent_by (str): Whether the message was sent by the user or the bot
            mime_type (str): The mime_type of the message
            message (str): The message itself

        Returns:
            dbc.Message: The message object with all the information from the database
        """
        if user.user_id is None:
            raise ValueError("User does not have a user_id. Cannot register message")
        message = dbc.Message(
            user_id=user.user_id,
            sent_by=sent_by,
            message_body=message_body,
            attachment_mime_type=attachment_mime_type,
            transcript=transcript,
            command=command,
        )
        return self.add_message(message)

    def get_draft(self, draft: dbc.Draft) -> dbc.Draft:
        return dbc.Draft(**self._get_obj_info("drafts", draft))

    def add_order(self, order: dbc.Order):
        for field in ["user_id", "draft_id", "address_id", "blob_path"]:
            if getattr(order, field) is None:
                raise ValueError(f"Order does not have a {field}. Cannot add order")
        duplicates = self._check_duplicates("orders", order)
        if duplicates:
            return 1, f"A existing user was already found with {', '.join(duplicates)}"
        else:
            r = self.client.table("orders").insert(order.to_dict()).execute()
            return 0, "Order added successfully"

    def get_order(self, order: dbc.Order):
        self._get_obj_info("orders", order)

    def register_draft(self, draft: dbc.Draft, pdf_bytes: bytes) -> dbc.Draft:
        if draft.user_id is None:
            raise ValueError("Draft does not have a user_id. Cannot register draft")

        # upload to supabase storage
        mime_type = "application/pdf"
        bucket_path = self.upload_file(pdf_bytes, draft.user_id, mime_type=mime_type)
        draft.blob_path = bucket_path
        return self.add_draft(draft)

    def update_system_messages(self):
        # easiest way to get all columns when the table is empty is inserting a dummy value
        self.client.table("system_messages").insert(
            {"full_message_name": "test"}
        ).execute()
        column_names = list(
            self.client.table("system_messages").select("*").execute().data[0].keys()
        )

        assert (
            "full_message_name" in column_names
        ), "Some change has been made to the database schema. The column 'full_message_name' is missing"

        # delete all values in table "system_messages"
        self.client.table("system_messages").delete().neq(
            "full_message_name", ""
        ).execute()

        # get data from google spreadhsheet and filter out columns that are not in the spreadsheet
        system_message_df = get_message_spreadsheet()
        system_message_df = system_message_df[
            [col for col in column_names if col in system_message_df.columns]
        ]

        insert_values = system_message_df.to_dict(orient="records")
        # Turn all emojis into a unicode escape representation
        # for my_dict in insert_values:
        #     for key, value in my_dict.items():
        #         if key != "full_message_name":
        #             # Check if the value is a string before applying encode
        #             if isinstance(value, str):
        #                 # we need to encode and decode to get rid of the "b" prefix.
        #                 my_dict[key] = value.encode("unicode_escape").decode()

        # insert all values from system_message_df into table "system_messages"
        self.client.table("system_messages").insert(insert_values).execute()

    def get_system_message(
        self, msg_name: str, col_name: str = cfg.MESSAGES_SHEET_NAME
    ) -> str:
        response = (
            self.client.table("system_messages")
            .select("*")
            .eq("full_message_name", msg_name)
            .execute()
        )
        if response.data == []:
            raise NoEntryFoundError("system_messages", col_name, msg_name)
        elif len(response.data) > 1:
            raise ValueError(
                f"More than one message found with {col_name} = {msg_name}"
            )
        # encode and decode to make sure that all emojis work
        # .encode("utf-8").decode('unicode_escape')
        return response.data[0][col_name]

    def get_last_user_message(self, user: dbc.User) -> dbc.Message:
        """Returns the last message sent by the user

        Args:
            user (User): The user to search for

        Returns:
            dbc.Message: The last message sent by the user
        """
        user = self.get_user(user)
        response = (
            self.client.table("messages")
            .select("*")
            .eq("user_id", user.user_id)
            .order("timestamp", desc=True)
            .execute()
        )
        data = response.data
        if len(data) == 0:
            assert user.user_id is not None
            raise NoEntryFoundError("messages", "user_id", str(user.user_id))
        else:
            return dbc.Message(**data[0])
