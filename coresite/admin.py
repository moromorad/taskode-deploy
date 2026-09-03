from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import Task, Project, EmailOTP, UserProfile, CodeChunk

# This registers your model so it shows up in the admin dashboard
@admin.register(Task)
class TaskAdmin(ImportExportModelAdmin):
    # This controls which columns are visible in the admin list view
    list_display = ('title', 'ticket_type', 'completed', 'project', 'created_at', "id")
    
    # This adds a handy filter sidebar on the right
    list_filter = ('completed', )
    
    # This adds a search bar to look up tasks by title
    search_fields = ('title',)

@admin.register(Project)
class ProjectAdmin(ImportExportModelAdmin):
    list_display = ('name','github_repo','ast_outline')
    
    # This adds a search bar to look up tasks by title
    search_fields = ('name',)

@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp_code', 'session_token', 'created_at', 'expires_at', 'attempts', 'is_used')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__username', 'user__email', 'session_token')

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'calendar_token', 'created_at')
    search_fields = ('user__username', 'user__email', 'calendar_token')

@admin.register(CodeChunk)
class CodeChunkAdmin(admin.ModelAdmin):
    list_display = ('project', 'filepath', 'symbol_type', 'start_line', 'end_line', 'chunk_id')
    list_filter = ('symbol_type', 'project')
    search_fields = ('filepath', 'text', 'chunk_id')