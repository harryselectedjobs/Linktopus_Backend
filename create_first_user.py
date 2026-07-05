from services.user_service import create_user

result = create_user("rahul@test.com", "Eightmay2000")
print(result)


# patch_user.py
# from services.user_service import _get_users_table
# import uuid
#
# table = _get_users_table()
# table.update_item(
#     Key={"email": "rahul@test.com"},
#     UpdateExpression="SET id = :id",
#     ExpressionAttributeValues={":id": str(uuid.uuid4())}
# )
# print("Patched")