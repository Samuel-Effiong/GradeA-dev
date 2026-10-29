from drf_spectacular.extensions import OpenApiSerializerExtension


class PolymorphicAssignmentExtension(OpenApiSerializerExtension):
    # Use the name of the component, not the file path
    target_class = "assignments.serializers.AssignmentListSerializer"

    def map_serializer(self, auto_schema, direction):
        return {
            "type": "object",
            "properties": {
                "user_type": {"type": "string", "enum": ["teacher", "student"]},
            },
            "required": ["user_type"],
            "discriminator": {
                "propertyName": "user_type",
                "mapping": {
                    "teacher": "#/components/schemas/AssignmentListSerializer",
                    "student": "#/components/schemas/AssignmentListStudentSerializer",
                },
            },
        }
