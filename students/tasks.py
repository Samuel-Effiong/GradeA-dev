# from celery import shared_task
#
# from assignments.models import Assignment

# from .services import upload_answers_engine

# @shared_task(bind=True, max_retries=3)
# def upload_single_submission_task(self, assignment_id, file_data, request_user_id, file_name):
#     """
#     Process a single file. If it fails, it returns an error dict
#     insteadof raising an exception to keep the Group Result clean.
#     """
#     assignment = Assignment.objects.get(id=assignment_id)
#     request_user = User.objects.get(id=request_user_id)
#
#     try:
#         submission = upload_answers_engine(
#             assignment=assignment,
#             content=file_data,
#             user
#         )
