// ============================================================
// V2 Config — per-child data & preferences
// ============================================================

const CONFIG = {
  children: {
    diya: {
      name: 'Diya',
      school: 'Headington School',
      year: 'Year 10',
      theme: 'light',
      accent: '#4f8cff',
      accent2: '#10b981',
      storageKeys: {
        streak: 'diya_streak',
        profile: 'profile_diya',
        tips: 'study_tips_viewed_diya',
        planner: 'planner_diya',
        tasks: 'tasks_diya',
        theme: 'theme_diya'
      },
      tipsPool: 'STUDY_TIPS_DIYA',  // defined in tips-data.js

      // --- Grades (Headington 2025-26 latest) ---
      subjects: [
        { name: 'Maths',          grade: 'Above 6/5', status: 'excellent', trend: 'Strongest core · UKMT SMC', bar: 85, tag: 'Above',     tagClass: 'green' },
        { name: 'FMaths',         grade: 'Enrolled',  status: 'excellent', trend: 'Short course from Y11',        bar: 80, tag: 'A-Level',   tagClass: 'green' },
        { name: 'Physics',        grade: 'Above 6/5', status: 'excellent', trend: 'Engineering foundation',        bar: 78, tag: 'Weak explain', tagClass: 'yellow' },
        { name: 'Chemistry',      grade: 'Grade 5',   status: 'meeting',   trend: 'Self-assessed weak',            bar: 62, tag: 'Needs Focus', tagClass: 'yellow' },
        { name: 'History',        grade: 'Grade 5',   status: 'meeting',   trend: 'Acceptable',                    bar: 60, tag: 'Stable',     tagClass: 'yellow' },
        { name: 'Drama',          grade: '6→6/5',     status: 'watching',  trend: 'Monitor decline',               bar: 55, tag: 'Watch',      tagClass: 'yellow' },
        { name: 'Art',            grade: 'GCSE',      status: 'meeting',   trend: 'Continuing GCSE',               bar: 65, tag: 'Non-priority', tagClass: 'neutral' },
        { name: 'English Lang',   grade: '4/3',       status: 'risk',      trend: 'Key weakness: analysis',        bar: 35, tag: 'CRITICAL',   tagClass: 'red' },
        { name: 'English Lit',    grade: '4/3',       status: 'risk',      trend: 'Needs improvement',             bar: 35, tag: 'CRITICAL',   tagClass: 'red' }
      ],

      // --- Strengths ---
      strengths: [
        { area: 'Maths + FMaths', detail: 'Strongest subjects. UKMT SMC training active. A-level ready.', icon: '📐' },
        { area: 'Physics', detail: 'Above grade. Engineering-linked interest. Practical work solid.', icon: '⚡' },
        { area: 'STEM Direction', detail: 'Clear Oxbridge Engineering path. Imperial target.', icon: '🎯' }
      ],

      // --- Weaknesses ---
      weaknesses: [
        { area: 'English (Lang + Lit)', detail: 'Grade 4/3 — drags down GCSE profile. Analytical writing weak. Affects A-level options.', severity: 'critical' },
        { area: 'Chemistry', detail: 'Grade 5 — self-reported as weak. Summer consolidation needed.', severity: 'medium' },
        { area: 'Physics "describe/explain"', detail: 'Strong on calculation, weak on written explanation. GCSE exam technique gap.', severity: 'medium' }
      ],

      // --- Risks ---
      risks: [
        { level: 'critical', text: 'English 4/3 — Below GCSE pass threshold for top universities', action: 'Summer intensive: academic writing + GCSE past papers' },
        { level: 'warning', text: 'Drama declining (6→5) — Could affect GCSE profile breadth', action: 'Monitor this term. Decide by summer if extra support needed.' },
        { level: 'info', text: 'Physics explanation gap — Written answers vs calculation', action: 'Practice "describe/explain" question types this summer' }
      ],

      // --- Countdowns ---
      countdowns: [
        { icon: '🏖️', name: 'Summer Break', target: '2026-07-01' },
        { icon: '📚', name: 'Year 11 Starts', target: '2026-09-01' },
        { icon: '🧮', name: 'UKMT SMC', target: '2026-10-01' },
        { icon: '📝', name: 'GCSE Exams Begin', target: '2027-05-12' },
        { icon: '🎓', name: 'A-Level Starts', target: '2027-09-01' }
      ],

      // --- Radar ---
      radar: {
        axes: ['Maths', 'FMaths', 'Physics', 'Chemistry', 'English', 'Computing'],
        values: [85, 80, 78, 62, 35, 55],
        max: 100
      },

      // --- Summer Plan milestones ---
      summerPlan: [
        { month: 'Jun', title: 'GCSE Year 10 Exams', desc: 'Complete summer term exams. Identify gaps.', done: false },
        { month: 'Jul W1-2', title: 'English Bootcamp', desc: 'Daily analytical writing + past papers. Target: 5 by September.', done: false },
        { month: 'Jul W3-4', title: 'Physics Explain Practice', desc: 'Focus on written explanation questions. Required practicals review.', done: false },
        { month: 'Aug W1-2', title: 'UKMT SMC Prep', desc: 'Past papers + problem-solving sessions. Target: Silver or above.', done: false },
        { month: 'Aug W3-4', title: 'Chemistry Consolidation', desc: 'Key topics review. Self-assessment against GCSE spec.', done: false },
        { month: 'Sep', title: 'Year 11 Start', desc: 'GCSE final year. UCAS exploration begins.', done: false }
      ],

      // --- Long-Term Route ---
      longTermRoute: [
        { period: 'Y10 Summer 2026', focus: 'English rescue + STEM foundations + UKMT', milestone: 'English to 5, SMC Silver' },
        { period: 'Y11 2026-27', focus: 'GCSE 887776+ · MAT intro · A-level decisions', milestone: 'GCSE target: 8 subjects, 7+ in English' },
        { period: 'Y12 2027-28', focus: 'A-level Maths+FMaths+Physics+4th · MAT prep', milestone: '4 AS levels, MAT practice' },
        { period: 'Y12 Summer 2028', focus: 'Work experience + personal statement draft', milestone: 'Engineering-related work experience' },
        { period: 'Y13 2028-29', focus: 'A-level final push · MAT · UCAS', milestone: 'A*A*A* target, MAT score 65+' },
        { period: 'Autumn 2029', focus: 'University start', milestone: 'Imperial Engineering or equivalent' }
      ]
    },

    jemma: {
      name: 'Jemma',
      school: 'Headington School',
      year: 'Year 8',
      theme: 'dark',
      accent: '#10b981',
      accent2: '#a78bfa',
      storageKeys: {
        streak: 'jemma_streak',
        profile: 'profile_jemma',
        tips: 'study_tips_viewed_jemma',
        planner: 'planner_jemma',
        tasks: 'tasks_jemma',
        theme: 'theme_jemma'
      },
      tipsPool: 'STUDY_TIPS_JEMMA',

      subjects: [
        { name: 'Maths',          grade: '95%',       status: 'excellent', trend: 'Exceptional · UK Bebras Distinction', bar: 95, tag: 'Top 10%',   tagClass: 'green' },
        { name: 'Chemistry',      grade: '97%',       status: 'excellent', trend: 'Exceptional · Top of year',          bar: 97, tag: 'Star',       tagClass: 'green' },
        { name: 'Biology',        grade: 'Above',     status: 'excellent', trend: 'Strong · Science foundation',         bar: 82, tag: 'Strong',     tagClass: 'green' },
        { name: 'Physics',        grade: 'Above',     status: 'excellent', trend: 'Strong · STEM pathway',               bar: 80, tag: 'Strong',     tagClass: 'green' },
        { name: 'Computing',      grade: 'Above',     status: 'excellent', trend: 'Programming + Bebras talent',         bar: 85, tag: 'Talent',     tagClass: 'green' },
        { name: 'English',        grade: 'Meeting',   status: 'meeting',   trend: 'Meeting expectations',                bar: 65, tag: 'OK',         tagClass: 'yellow' },
        { name: 'German',         grade: 'Meeting',   status: 'meeting',   trend: 'Meeting expectations',                bar: 65, tag: 'OK',         tagClass: 'yellow' },
        { name: 'Geography',      grade: 'Meeting',   status: 'meeting',   trend: 'Steady progress',                     bar: 65, tag: 'OK',         tagClass: 'yellow' },
        { name: 'Art',            grade: 'Above',     status: 'excellent', trend: 'Creative strength',                   bar: 78, tag: 'Creative',   tagClass: 'green' }
      ],

      strengths: [
        { area: 'Maths 95%', detail: 'Exceptional. UK Bebras Distinction (top 10%). Ready for advanced challenges.', icon: '📐' },
        { area: 'Chemistry 97%', detail: 'Top performer. Natural talent for science. Lab work excellent.', icon: '🧪' },
        { area: 'Computing', detail: 'Programming + Bebras talent. Problem-solving mindset.', icon: '💻' }
      ],

      weaknesses: [
        { area: 'Essay subjects', detail: 'English + German at "Meeting". Written expression developing.', severity: 'medium' },
        { area: 'Geography', detail: 'Steady but not outstanding. Needs more engagement with data analysis.', severity: 'low' }
      ],

      risks: [
        { level: 'info', text: 'Year 8 — Early foundation building phase', action: 'No immediate risks. Focus on exploration + curiosity.' },
        { level: 'warning', text: 'Essay writing needs early attention', action: 'Build reading habit now. Creative writing practice.' }
      ],

      radar: {
        axes: ['Maths', 'Chemistry', 'Biology', 'Physics', 'Computing', 'English'],
        values: [95, 97, 82, 80, 85, 65],
        max: 100
      },

      // --- Countdowns ---
      countdowns: [
        { icon: '🏖️', name: 'Summer Holidays', target: '2026-07-15' },
        { icon: '📚', name: 'Year 9 Starts', target: '2026-09-01' },
        { icon: '💻', name: 'Bebras 2026', target: '2026-11-01' },
        { icon: '🧮', name: 'UKMT JMC', target: '2027-04-25' },
        { icon: '📝', name: 'GCSE Start', target: '2029-05-01' }
      ],

      summerPlan: [
        { month: 'Jun', title: 'Year 8 Exams', desc: 'Complete exams. School trip planning.', done: false },
        { month: 'Jul W1-2', title: 'Programming Projects', desc: 'Python/micro:bit projects. Bebras prep.', done: false },
        { month: 'Jul W3-4', title: 'Science Exploration', desc: 'Chemistry experiments + science museum visits.', done: false },
        { month: 'Aug W1-2', title: 'Creative Writing', desc: 'Build essay skills. Reading challenge: 5 books.', done: false },
        { month: 'Aug W3-4', title: 'Maths Challenge', desc: 'UKMT Junior prep. Problem-solving puzzles.', done: false },
        { month: 'Sep', title: 'Year 9 Start', desc: 'GCSE options exploration begins.', done: false }
      ],

      longTermRoute: [
        { period: 'Y8-9 2026-28', focus: 'Exploration + foundation · GCSE options', milestone: 'Identify strengths + interests' },
        { period: 'Y10-11 2028-30', focus: 'GCSE focus · STEM depth', milestone: 'Strong GCSE profile (8+ subjects)' },
        { period: 'Y12-13 2030-32', focus: 'A-levels + university prep', milestone: 'Oxbridge/Imperial STEM target' },
        { period: 'Autumn 2032', focus: 'University start', milestone: 'Top STEM programme' }
      ]
    }
  },

  // --- Active child (overridden by page) ---
  active: 'diya',

  // --- Route data (shared) ---
  routes: {
    oxbridge: ['Cambridge Engineering', 'Oxford Engineering Science', 'Cambridge Design'],
    imperial: ['Imperial Design Engineering', 'Imperial Mechanical Engineering'],
    safety: ['UCL Engineering', 'Bristol Engineering', 'Southampton Engineering']
  }
};
