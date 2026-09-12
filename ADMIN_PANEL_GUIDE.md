# Open WebUI Admin Panel Guide

Welcome to the Admin Panel! This guide will walk you through the essential tools you need to create experiment workflows, manage user groups, and analyze participant sessions.

## 1. Admin Panel Tour & Navigation

The Admin Panel is your central hub for managing studies and participants. 

![Admin Panel Tour](docs/images/admin_guide/01_admin_panel_tour.png)

At the top of the page, you'll find the primary navigation bar:
- **Research Dashboard**: View study progress, participant activity, and overall results. You can switch between different tabs like Overview, Participants, Essays, and Survey Results to get specific insights. You can also filter these views by User Group or Experiment State.
- **Users**: Manage participant accounts and create User Groups to organize them.
- **Experiment Tasks**: Create the individual tasks (essays, questions, surveys) and assemble them into full experiment workflows.

---

## 2. Researcher Dashboard & Participant Sessions

The Researcher Dashboard allows you to monitor participant progress and download their session data for further analysis.

### Exporting Sessions
Navigate to **Research Dashboard** &rarr; **Participants** to see a list of everyone assigned to your studies.

![Dashboard Session Exports](docs/images/admin_guide/02_dashboard_export_sessions.png)

- Select individual participants or use the header checkbox to select everyone.
- If you need to protect participant privacy, toggle the **Anonymize account identifiers** option to hide emails and usernames.
- Click **Export full sessions (JSON)** to download the data for the selected participants.

### Session Inspector
Click on any participant's row to open the Session Inspector.

![Participant Session Detail](docs/images/admin_guide/03_participant_session_detail.png)

This detailed view gives you a step-by-step timeline of the participant's session, including when they accepted consent, submitted surveys, and started writing. You can also see a live preview of their essay drafts and final submissions.

---

## 3. User Groups

User Groups allow you to organize participants into different study conditions or cohorts (e.g., a Control Group and a Test Group).

![User Groups Management](docs/images/admin_guide/04_user_groups_management.png)

Navigate to **Users** &rarr; **Groups** to view all your existing groups. 

![Configure User Group Modal](docs/images/admin_guide/05_configure_user_group_modal.png)

To create a new group, click **+ New Group**. Give the group a clear name and description. You can also adjust specific permissions for the group and assign users to it directly from this menu.

---

## 4. Creating Experiment Tasks

Before you can build a complete study workflow, you need to create the individual tasks. Navigate to **Experiment Tasks** to get started.

### Essay Tasks
Essay tasks define the writing prompts and instructions for your participants.

![Essay Tasks Authoring](docs/images/admin_guide/06_task_authoring_essay.png)

Give your topic a title and use the text editor to write out the prompt, background context, and guidelines. You can use the **Preview** tab to see how it will look to participants before saving.

### Question Tasks
Question tasks let you create comprehension or knowledge tests.

![Question Tasks Authoring](docs/images/admin_guide/07_task_authoring_question.png)

You can choose from several formats, including Single Choice, Multiple Select, Fill in the Blank, or Free Text. You can also assign point values and attach images to your questions.

### Survey Tasks
Survey tasks are used for pre-study or post-study questionnaires.

![Survey Tasks Authoring](docs/images/admin_guide/08_task_authoring_survey.png)

You can create 1–5 scale questions, multiple-choice questions, or text inputs. You can customize the labels for the scales and choose whether a question is required.

---

## 5. Creating Experiment Workflows

Workflows combine your individual tasks into a step-by-step study pipeline.

![Experiment Workflow Library](docs/images/admin_guide/09_experiment_workflow_library.png)

Navigate to **Experiment Tasks** &rarr; **Workflows**. Here you will see your Workflow Library, which lists all your existing workflows. Click **+ New Workflow** to create a new one.

![Create Experiment Workflow](docs/images/admin_guide/10_create_experiment_workflow.png)

In the Workflow Editor, give your study a name and description. You can set the rules for how participants progress through the study, such as whether they must go in order (Strict sequential) or can jump around (Free navigation). You can also choose if participants must agree to a consent form first.

![Workflow Ordered Tasks](docs/images/admin_guide/11_workflow_ordered_tasks.png)

Scroll down to the **Ordered tasks** section to add stages to your workflow. Click **+ Essay**, **+ Question Task**, or **+ Survey** to add the tasks you created earlier. You can use the up and down arrows to easily reorder them.

---

## 6. Downloading and Uploading Workflows

Workflows can be easily saved to your computer or shared with others.

- **Downloading (Exporting)**: From the Workflow Library or the Workflow Editor, click the **Export** button. This will download a `.zip` file containing your workflow and all of its associated tasks.
- **Uploading (Importing)**: In the Workflow Library, click the **Import** button and upload a `.zip` file to instantly restore a workflow and all of its tasks.

---

## 7. Applying Workflows to User Groups

Once your workflow is ready, you need to apply it to a User Group so participants can access it.

![Export and Apply Workflow](docs/images/admin_guide/12_export_and_apply_workflow.png)

In the Workflow Editor, click the **Apply** button at the top right. A menu will appear where you can select the target User Group. Once you click **Confirm and publish**, the workflow will be active for all participants in that group.
