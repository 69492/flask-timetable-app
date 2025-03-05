

from flask import Flask, render_template_string, request, send_file, redirect, url_for
import pandas as pd
import threading
import webbrowser
from ortools.sat.python import cp_model
import os
from werkzeug.utils import secure_filename
import random

app = Flask(__name__)

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Sample dataset file (Provide an actual path or remove if unnecessary)
SAMPLE_DATASET_PATH ="C:/Users/DELL/OneDrive/Documents/CSPDATA1sample - CSM.xlsx"

# Function to check if the file extension is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to generate the timetable
def generate_timetable(file_path):
    try:
        # Load Excel file
        df_sections = pd.read_excel(file_path, sheet_name="Sections Data")
        df_subjects = pd.read_excel(file_path, sheet_name="Subjects Data")
        df_teachers = pd.read_excel(file_path, sheet_name="Teachers Data")
        df_timeslots = pd.read_excel(file_path, sheet_name="Time Slot Data")
        df_section_subjects = pd.read_excel(file_path, sheet_name="Section Subjects Data")
        df_fixed_activities = pd.read_excel(file_path, sheet_name="Fixed Activities")
        df_lab_sessions = pd.read_excel(file_path, sheet_name="Lab Sessions")
        df_Weekly_Once = pd.read_excel(file_path, sheet_name="WeeklyOnce Subjects")
        
        # Extract necessary data
        sections = df_sections.apply(lambda row: f"{row['Year']}_{row['Department']}_{row['Section']}", axis=1).tolist()
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        timeslots_data = df_timeslots.to_dict(orient="records")
        all_slots = [slot["Slot ID"] for slot in timeslots_data]
        years = df_sections["Year"].astype(str).unique().tolist()        
        subject_dict = dict(zip(df_subjects['Subject ID'], df_subjects['Subject Name']))
        
        # Mapping sections to subjects and assigned faculty
        section_subject_mapping = {}
        for _, row in df_section_subjects.iterrows():
            section_key = f"{row['Year']}_{row['Department']}_{row['Section']}"
            section_subject_mapping.setdefault(section_key, []).append((row["Subject ID"], row["Faculty ID"]))
        
        # Mapping sections to lab sessions
        section_lab_mapping = {}
        for _, row in df_lab_sessions.iterrows():
            section_key = f"{row['Year']}_{row['Department']}_{row['Section']}"
            section_lab_mapping.setdefault(section_key, []).append((row["Subject ID"], row["Faculty ID"]))
        
        # Fixed activity mapping
        fixed_activities = {}
        for _, row in df_fixed_activities.iterrows():
            section_key = f"{row['Year']}_{row['Department']}_{row['Section']}"
            day, slot_id, activity = row["Day"], row["Slot ID"], row["Activity"]
            fixed_activities.setdefault(section_key, {}).setdefault(day, {})[slot_id] = activity
        
        # Define OR-Tools model
        model = cp_model.CpModel()
        
        # Define variables
        is_subject_assigned = {}
        WeeklyOnce_vars = {}
        is_lab_assigned = {}
        
        # Precompute slot sets
        all_slots = {slot["Slot ID"] for slot in timeslots_data}
        break_slots = {slot["Slot ID"] for slot in timeslots_data if slot.get("Break Type", "None") in ["Break", "Lunch"]}
        
        # Step 2: Assign Lab Sessions (Ensure Consecutive Slots)
        for section in sections:
            year, dept, sec = section.split("_")  # Extract individual components
            
            if section not in section_lab_mapping:  
                continue  # Skip sections with no lab subjects
            
            for subject_id, faculty_id in section_lab_mapping[section]:
                is_lab_assigned[(section, subject_id)] = []
                
                for day in days:
                    for i in range(len(timeslots_data) - 1):
                        slot1 = timeslots_data[i]["Slot ID"]
                        slot2 = timeslots_data[i + 1]["Slot ID"]
        
                        if slot1 in break_slots or slot2 in break_slots or slot1 in fixed_activities.get(section, {}).get(day, {}):
                            continue  # Skip breaks and fixed slots
        
                        var = model.NewBoolVar(f"lab_{section}_{subject_id}_{day}_{slot1}_{slot2}")
                        is_lab_assigned[(section, subject_id)].append((day, slot1, slot2, var))
        
                        # Assign labs only if the teacher is available
                        is_subject_assigned[(section, subject_id, day, slot1)] = var
                        is_subject_assigned[(section, subject_id, day, slot2)] = var
        
        # Prevent lab allocation during fixed activities
        for section, day_slots in fixed_activities.items():
            for day, blocked_slots in day_slots.items():
                for slot_id in blocked_slots:
                    for subject_id, _ in section_lab_mapping.get(section, []):
                        for d, s1, s2, var in is_lab_assigned.get((section, subject_id), []):
                            if d == day and (s1 == slot_id or s2 == slot_id):
                                model.Add(var == 0)
        
        # Constraint: Prevent a Faculty from Teaching Multiple Lab Sections Simultaneously
        for day in days:
            for i in range(len(timeslots_data) - 1):
                overlapping_labs = []
                for (section, subject_id), slots in is_lab_assigned.items():
                    for d, s1, s2, var in slots:
                        if d == day and timeslots_data[i]["Slot ID"] == s1 and timeslots_data[i + 1]["Slot ID"] == s2:
                            overlapping_labs.append(var)
        
                if overlapping_labs:
                    model.Add(sum(overlapping_labs) <= 1)  # Prevent lab overlap
        
        # Constraint: No two sections share the same lab session at the same time
        for day in days:
            for i in range(len(timeslots_data) - 1):
                overlapping_labs = []
                for (section, subject_id), slots in is_lab_assigned.items():
                    for d, s1, s2, var in slots:
                        if d == day and timeslots_data[i]["Slot ID"] == s1 and timeslots_data[i + 1]["Slot ID"] == s2:
                            overlapping_labs.append(var)
                if overlapping_labs:
                    model.Add(sum(overlapping_labs) <= 1)
        
        # Ensure no multiple lab sessions on the same day per section
        for section, labs in section_lab_mapping.items():
            for day in days:
                lab_sessions_for_day = []
                
                for subject_id, faculty_id in labs:
                    for d, s1, s2, lab_var in is_lab_assigned.get((section, subject_id), []):
                        if d == day:
                            lab_sessions_for_day.append(lab_var)
                
                if lab_sessions_for_day:
                    model.AddAtMostOne(lab_sessions_for_day)
        
        # Ensure each lab session is assigned exactly once per week per section
        for (section, subject_id), slots in is_lab_assigned.items():
            if slots:
                model.AddExactlyOne(var for _, _, _, var in slots)
        
        # Step 1: Assign Weekly Once Subjects to Available Slots (Filtered by Year)
        for section in sections:
            WeeklyOnce_vars[section] = {}
        
            # Extract year from section name (assuming format: 'II_CSM_A' → 'II')
            section_parts = section.split('_')
            year = section_parts[0] if len(section_parts) >= 2 else None
        
            if not year:
                print(f"Warning: Could not determine year for section {section}")
                continue
        
            # Filter Weekly Once subjects only for the given year
            relevant_Weekly_Once = df_Weekly_Once[df_Weekly_Once['Year'] == year]
        
            for _, subject_row in relevant_Weekly_Once.iterrows():
                subject_id = subject_row['Subject ID']
                available_slots = []
        
                for day in days:
                    for slot in all_slots:
                        if slot in break_slots or slot in fixed_activities.get(section, {}).get(day, {}):
                            continue  # Skip break & fixed slots
                        available_slots.append((day, slot))
        
                if available_slots:
                    # Randomly choose ONE slot per subject per section
                    selected_day, selected_slot = random.choice(available_slots)
                    var = model.NewBoolVar(f"weekly_{section}_{subject_id}_{selected_day}_{selected_slot}")
                    WeeklyOnce_vars[section][subject_id] = (selected_day, selected_slot, var)
                    is_subject_assigned[(section, subject_id, selected_day, selected_slot)] = var
                else:
                    print(f"Warning: No available slot for Weekly Once {subject_id} in section {section}")
        
        # Step 2: Prevent Weekly Once from Overlapping with Labs
        for section in sections:
            for day in days:
                for slot_id in all_slots:
                    lab_vars_in_slot = []
                    Weekly_Once_vars_in_slot = []
        
                    for subj, faculty_id in section_lab_mapping.get(section, []):
                        if (section, subj) in is_lab_assigned:
                            for d, s1, s2, var in is_lab_assigned.get((section, subj), []):
                                if d == day and (s1 == slot_id or s2 == slot_id):
                                    lab_vars_in_slot.append(var)
        
                    for subj, (d, s, var) in WeeklyOnce_vars[section].items():
                        if d == day and s == slot_id:
                            Weekly_Once_vars_in_slot.append(var)
                    
                    if lab_vars_in_slot and Weekly_Once_vars_in_slot:
                        model.AddAtMostOne(lab_vars_in_slot + Weekly_Once_vars_in_slot)
        
        # Step 3: Ensure No Two Weekly Once Subjects are in the Same Slot Within a Section
        for section in sections:
            for day in days:
                for slot_id in all_slots:
                    slot_vars = [
                        var for subj, (d, s, var) in WeeklyOnce_vars[section].items() if d == day and s == slot_id
                    ]
                    if len(slot_vars) > 1:
                        model.AddAtMostOne(slot_vars)
        
        # Step 4: Ensure Each Weekly Once Subject is Assigned Exactly Once per Week
        for section in sections:
            for subject_id in df_Weekly_Once['Subject ID'].unique():
                if subject_id in WeeklyOnce_vars[section]:
                    selected_day, selected_slot, var = WeeklyOnce_vars[section][subject_id]
                    model.AddExactlyOne([var])  # Ensure the subject appears only once per week
        
        # Step 3: Assign Regular Subjects (Checking Across All Years)
        for section in sections:
            year, dept, sec = section.split("_")
            section_key = f"{year}_{dept}_{sec}" 
        
            for subject_id, faculty_id in section_subject_mapping.get(section_key, []):  
                assigned = False  # Track if subject is assigned at least once
                for day in days:
                    for slot in all_slots:
                        # Skip if the slot is in break or fixed activities
                        if slot in break_slots or slot in fixed_activities.get(section_key, {}).get(day, {}):
                            continue  
        
                        # Check if the slot is already taken by lab subjects
                        lab_conflicts = [
                            var for (d, s1, s2, var) in is_lab_assigned.get((section_key, subject_id), [])
                            if d == day and (slot == s1 or slot == s2)  # Correct format
                        ]
                        
                        if lab_conflicts:
                            print(f"🚫 Conflict: Lab already assigned for {subject_id} in {section_key} on {day}, Slot {slot}")
                            continue  # Skip since this slot is taken by a lab
        
                        # Skip if the slot is already taken by Weekly Once subjects
                        WeeklyOnce_conflicts = [
                            var for subj, (d, s, var) in WeeklyOnce_vars.get(section_key, {}).items()
                            if d == day and s == slot
                        ]
                        if WeeklyOnce_conflicts:
                            continue  # Skip since this slot is taken by a Weekly Once subject
                        
                        # Create a new variable for this subject assignment
                        var = model.NewBoolVar(f"subject_{section_key}_{subject_id}_{day}_{slot}")
                        is_subject_assigned[(section_key, subject_id, day, slot)] = var
                        assigned = True  # Mark subject as assignable
        
                # Debug: Check if a subject has at least 1 available slot
                if not assigned:
                    print(f"⚠ WARNING: No available slots for {subject_id} in {section_key}")
        
        # Ensure at most one subject is assigned per section per slot
        for day in days:
            for slot in all_slots:
                for section in sections:
                    section_key = f"{section}"
                    subject_vars = [
                        is_subject_assigned[(section_key, subject_id, day, slot)]
                        for subject_id, _ in section_subject_mapping.get(section_key, [])
                        if (section_key, subject_id, day, slot) in is_subject_assigned
                    ]
                    if subject_vars:  # Prevent empty list errors
                        model.AddAtMostOne(subject_vars)  # Only one subject per slot per section
        
        # Loosen Constraint: No subject should be assigned more than 2 slots per day
        for section in sections:
            section_key = f"{section}"
            for subject_id, _ in section_subject_mapping.get(section_key, []):
                for day in days:
                    subject_day_vars = [
                        is_subject_assigned[(section_key, subject_id, day, slot_id)]
                        for slot_id in all_slots
                        if (section_key, subject_id, day, slot_id) in is_subject_assigned
                    ]
                    if subject_day_vars:  # Prevent empty list errors
                        model.Add(sum(subject_day_vars) <= 2)  # Allow up to 2 slots per day
        
        # Loosen Weekly Constraint: Allow 4-5 slots instead of exactly 5
        for section in sections:
            section_key = f"{section}"
            for subject_id, _ in section_subject_mapping.get(section_key, []):
                assigned_vars = [
                    is_subject_assigned[(section_key, subject_id, day, slot)]
                    for day in days
                    for slot in all_slots
                    if (section_key, subject_id, day, slot) in is_subject_assigned
                ]
                if assigned_vars:  # Prevent empty list errors
                    model.Add(sum(assigned_vars) >= 5)  # Allow 4-5 slots per week
        
        # Define penalty variables for faculty conflicts
        penalty_vars = []
        penalty_weight = 5  # Adjust this weight as needed
        
        for day in days:
            for slot in all_slots:
                faculty_conflict_vars = {}
        
                for section in sections:
                    section_key = f"{section}"  # Ensure section key is consistent
                    for subject_id, faculty_id in section_subject_mapping.get(section_key, []):
                        if faculty_id and (section_key, subject_id, day, slot) in is_subject_assigned:
                            faculty_conflict_vars.setdefault(faculty_id, []).append(
                                is_subject_assigned[(section_key, subject_id, day, slot)]
                            )
        
                for faculty_id, conflict_vars in faculty_conflict_vars.items():
                    if len(conflict_vars) > 1:
                        # Create a penalty variable that activates if there is a conflict
                        penalty_var = model.NewBoolVar(f"faculty_conflict_{faculty_id}_{day}_{slot}")
        
                        # Ensure the conflict constraint is properly enforced
                        model.Add(sum(conflict_vars) > 1).OnlyEnforceIf(penalty_var)
                        model.Add(sum(conflict_vars) <= 1).OnlyEnforceIf(penalty_var.Not())
        
                        penalty_vars.append(penalty_var)
        
        # Minimize faculty conflicts
        model.Minimize(penalty_weight * sum(penalty_vars))    
        
        # Solve model
        solver = cp_model.CpSolver()
        status = solver.Solve(model)
        
        # Generate timetable output
        if status in [cp_model.FEASIBLE, cp_model.OPTIMAL]:
            timetable_dict = {}
        
            for year in years:
                year_sections = [sec for sec in sections if sec.startswith(f"{year}_")]
        
                for section in year_sections:
                    df_timetable = pd.DataFrame(index=days, columns=[ts["Slot ID"] for ts in timeslots_data])
        
                    free_slot_count = 0  # Track free periods per section
        
                    for day in days:
                        for timeslot in timeslots_data:
                            slot_id = timeslot["Slot ID"]
                            break_type = timeslot.get("Break Type", "None")
                            slot_assigned = False  # Track slot assignment
        
                            # Handle breaks and lunch
                            if break_type in ["Break", "Lunch"]:
                                df_timetable.at[day, slot_id] = break_type
                                continue
        
                            # Handle fixed activities
                            if section in fixed_activities and day in fixed_activities[section]:
                                if slot_id in fixed_activities[section][day]:
                                    df_timetable.at[day, slot_id] = fixed_activities[section][day][slot_id]
                                    continue
        
                            # Assign lab sessions first
                            if section in section_lab_mapping:
                                for subject_id, _ in section_lab_mapping[section]:
                                    if (section, subject_id) in is_lab_assigned:
                                        for d, s1, s2, var in is_lab_assigned[(section, subject_id)]:
                                            if d == day and (s1 == slot_id or s2 == slot_id) and solver.Value(var) == 1:
                                                df_timetable.at[day, slot_id] = f"{subject_dict.get(subject_id, 'Unknown Lab')} (Lab)"
                                                slot_assigned = True
                                                break
                                    if slot_assigned:
                                        break
        
                            # Assign Weekly Once next
                            if not slot_assigned and section in WeeklyOnce_vars:
                                for subject_id, (d, s, var) in WeeklyOnce_vars[section].items():
                                    if d == day and s == slot_id and solver.Value(var) == 1:
                                        df_timetable.at[day, slot_id] = f"{subject_dict.get(subject_id, 'Unknown Weekly Once')}"
                                        slot_assigned = True
                                        break
        
                            # Assign regular subjects (prioritize section subjects first)
                            if not slot_assigned:
                                for subject_id, faculty_id in section_subject_mapping.get(section, []):
                                    key = (section, subject_id, day, slot_id)
                                    if key in is_subject_assigned and solver.Value(is_subject_assigned[key]) == 1:
                                        df_timetable.at[day, slot_id] = f"{subject_dict.get(subject_id, 'Unknown Subject')}"
                                        slot_assigned = True
                                        break
        
                            # If still empty, mark as "Free" (Ensure max one free slot per week)
                            if not slot_assigned:
                                if free_slot_count < 1:  # Limit to one free period per week
                                    df_timetable.at[day, slot_id] = "Free"
                                    free_slot_count += 1
                                else:
                                    df_timetable.at[day, slot_id] = "Unallocated   "
        
                    timetable_dict[section] = df_timetable
        
            # Function to fill "Unallocated   " slots in the timetable
            def fill_unallocated_slots(df_timetable, section_key, section_subject_mapping, subject_dict, file_path):
                # Load Target Subjects from Excel file
                df_target_subjects = pd.read_excel(file_path, sheet_name="Target Subjects")
        
                # Ensure the column exists
                if "Target Subjects" not in df_target_subjects.columns:
                    raise ValueError("  'Target Subjects' column not found in the Excel file.")
        
                target_subjects = set(df_target_subjects["Target Subjects"])
        
                # Count occurrences of each subject in the current timetable
                subject_counts = df_timetable.stack().value_counts().to_dict()
        
                # Ensure all subjects are included with a default count of 0
                for subject in target_subjects:
                    subject_counts.setdefault(subject, 0)
        
                # Helper function: Check if a subject is available for the current slot
                def is_valid_assignment(subject, day):
                    return (
                        subject_counts[subject] < 5 and  # Ensure subject does not exceed 5 times a week
                        (df_timetable.loc[day] == subject).sum() < 2  # Ensure subject does not repeat more than 2 times a day
                    )
        
                # Fill the timetable
                for day in df_timetable.index:
                    for slot in df_timetable.columns:
                        if df_timetable.at[day, slot] == "Unallocated   ":
                            assigned = False
        
                            # Iterate over all section subjects
                            for subject_id, faculty_id in section_subject_mapping.get(section_key, []):
                                subject = subject_dict.get(subject_id, "Unknown Subject")
        
                                if subject == "Unknown Subject":
                                    print(f"  Warning: Subject ID {subject_id} not found in subject_dict for {section_key}")
        
                                # Validate subject and assign if valid
                                if is_valid_assignment(subject, day):
                                    df_timetable.at[day, slot] = subject
                                    subject_counts[subject] += 1
                                    assigned = True
                                    break  # Move to the next slot
        
                            # If no valid subject is found, assign "Free Period"
                            if not assigned:
                                df_timetable.at[day, slot] = "Free Period"
        
                return df_timetable
        
            # Apply the function to fill unallocated slots for each section
            for section_key, df_timetable in timetable_dict.items():
                timetable_dict[section_key] = fill_unallocated_slots(df_timetable, section_key, section_subject_mapping, subject_dict, file_path)
        
            # Print updated timetables
            for section_key, df_timetable in timetable_dict.items():
                print(f"\n📅 Timetable for Section: {section_key}\n")
                print(df_timetable)
                print("\n" + "=" * 50 + "\n")
        
                # Save the updated timetable in the dictionary
                timetable_dict[section_key] = df_timetable
        
            # Combine all section timetables into one HTML output
            timetable_html = ""
            for section_key, df_timetable in timetable_dict.items():
                timetable_html += f"<h2>Timetable for {section_key}</h2>\n"
                timetable_html += df_timetable.to_html(classes="table table-bordered") + "<br><br>"
            
            return timetable_html

        
        else:
            return "<p>No feasible solution found.</p>"
        
    except Exception as e:
        return f"<p>Error: {str(e)}</p>"
# HTML Frontend Code
HTML_CODE = """


<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Timetable Generator</title>

    <!-- External Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@600&family=Roboto:wght@400&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">

    <style>
        /* General Styles */
        body {
            font-family: 'Roboto', sans-serif;
            background: linear-gradient(to right, #f8f9fa, #e3eaf2); /* Light Gradient */
            padding-top: 60px; /* Space for sticky navbar */
        }

        /* Sticky Navbar */
        .navbar {
            position: fixed;
            top: 0;
            width: 100%;
            background: transparent;
            transition: background 0.3s ease-in-out;
            padding: 15px 0;
            font-family: 'Poppins', sans-serif;
        }

        .navbar.scrolled {
            background: rgba(0, 0, 0, 0.8);
        }

        .navbar-brand {
            font-weight: bold;
            color: white !important;
        }

        .nav-link {
            color: white !important;
            transition: 0.3s;
        }

        .nav-link:hover {
            transform: scale(1.1);
        }

        /* Hero Section */
        .hero {
            background: url('https://source.unsplash.com/1600x900/?study,books') no-repeat center center/cover;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            text-align: center;
            position: relative;
            color: white;
            font-family: 'Poppins', sans-serif;
        }

        .hero::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.6);
        }

        .hero-content {
            position: relative;
            z-index: 1;
        }

        .hero h1 {
            font-size: 3rem;
            font-weight: bold;
        }

        /* Main Content */
        .container {
            background: white;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
            max-width: 900px;
            margin: auto;
            transform: translateY(-50px);
            opacity: 0;
            animation: fadeIn 1s ease-in-out forwards;
        }

        @keyframes fadeIn {
            to {
                transform: translateY(0);
                opacity: 1;
            }
        }

        /* Rules List */
        .rules-list {
            list-style-type: lower-alpha;
            padding-left: 20px;
        }

        /* Buttons */
        .myButton {
            background: linear-gradient(to bottom, #1538d4 5%, #ffae4c 100%);
            border-radius: 28px;
            border: 1px solid #eeb44f;
            display: inline-block;
            cursor: pointer;
            color: white;
            font-family: 'Poppins', sans-serif;
            font-size: 17px;
            padding: 16px 31px;
            text-decoration: none;
            transition: 0.3s ease-in-out;
        }

        .myButton:hover {
            background: linear-gradient(to bottom, #ffae4c 5%, #1538d4 100%);
            transform: scale(1.05);
        }

        /* Footer */
        .footer {
            text-align: center;
            padding: 15px;
            background: #000;
            color: white;
            margin-top: 50px;
        }

        /* Responsive */
        @media (max-width: 768px) {
            .hero h1 {
                font-size: 2rem;
            }
        }
    </style>
</head>
<body>

    <!-- Navbar -->
    <nav class="navbar navbar-expand-lg navbar-dark">
        <div class="container">
            <a class="navbar-brand" href="#">Timetable Generator</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item"><a class="nav-link" href="#">Home</a></li>
                    <li class="nav-item"><a class="nav-link" href="#">Features</a></li>
                    <li class="nav-item"><a class="nav-link" href="#">Contact</a></li>
                </ul>
            </div>
        </div>
    </nav>

    <!-- Hero Section -->
    <div class="hero">
        <div class="hero-content">
            <h1>Timetable Generator</h1>
            <p>Create structured timetables effortlessly!</p>
            <a href="#" class="myButton">Get Started</a>
        </div>
    </div>

    <!-- Main Content -->
    <div class="container">
        <h2>Rules to Follow:</h2>
        <ul class="rules-list">
            <li>First verify the sample dataset by downloading it.</li>
            <li>Ensure the format matches the required structure.</li>
            <li>Upload the timetable Excel file for processing.</li>
            <li>Review the generated timetable and download it.</li>
        </ul>

        <h2>Download Sample Dataset</h2>
        <button onclick="downloadFile()" class="myButton">Download Sample</button>

        <h2>Upload Timetable File</h2>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="file" class="form-control" required>
            <br>
            <button type="submit" class="myButton">Generate Timetable</button>
        </form>
        <hr>
        <div>{{ timetable|safe }}</div>

    </div>

    <!-- Footer -->
    <div class="footer">@2025 - AIHUB@VVIT</div>

    <script>
        function downloadFile() {
            window.location.href = "/download";
        }
    </script>

    <script>
        // Sticky Navbar Effect on Scroll
        window.addEventListener('scroll', function() {
            let navbar = document.querySelector('.navbar');
            if (window.scrollY > 50) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        });
    </script>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>

</body>
</html>


"""

@app.route("/", methods=["GET"])
def home():
    return render_template_string(HTML_CODE, timetable="")

@app.route("/download")
def download():
    return send_file(SAMPLE_DATASET_PATH, as_attachment=True)

@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return redirect(url_for("home"))

    file = request.files["file"]
    if file.filename == "":
        return redirect(url_for("home"))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(file_path)

        timetable = generate_timetable(file_path)
        return render_template_string(HTML_CODE, timetable=timetable)

    return redirect(url_for("home"))



if __name__ == "__main__":
    app.run(port=1000, debug=False)


