const { createApp, ref, computed, onMounted, nextTick } = Vue;

createApp({
  setup() {
    // Navigation & View State
    const currentNav = ref('home');       // 'home' | 'students' | 'entry' | 'sheets' | 'kb'
    const fastEntryMode = ref('tick');    // 'tick' | 'ocr'
    const isSubmitting = ref(false);

    // Current Date String for Header & Home
    const currentDateStr = computed(() => {
      const now = new Date();
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const day = String(now.getDate()).padStart(2, '0');
      const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
      const weekday = days[now.getDay()];
      return `${year} / ${month} / ${day} ${weekday}`;
    });

    // Overview state
    const overviewData = ref({
      critical_count: 0,
      passed_rate: 0,
      top_error_tags: [],
      students: []
    });

    // Fast Entry Tab A state
    const examList = ref([]);
    const selectedExamId = ref(null);
    const examQuestions = ref([]);
    const currentStudentId = ref(null);
    const tickScore = ref(52);
    const tickedMistakes = ref([]); // list of { q_no, exam_question_id, kb_mother_id, module_tag, error_tags }

    // Fast Entry Tab B state (OCR)
    const isOcrLoading = ref(false);
    const ocrParsedResults = ref([]);
    const ocrStudentId = ref(null);

    // Tracking state
    const trackingStudentId = ref(null);
    const studentDetail = ref(null);
    const showWechatModal = ref(false);
    const wechatData = ref({ teacher_summary: '', wechat_msg: '' });

    // Sheets & Monday Check state
    const sheetsList = ref([]);
    const showMondayModal = ref(false);
    const currentSheetDetail = ref(null);
    const mondayCheckStates = ref({}); // { item_id: bool }
    const showNewSheetModal = ref(false);
    const newBatchTitle = ref('周末数学冲刺巩固单');
    const selectedStudentIds = ref([]);

    // Preview Image Modal state
    const previewImageUrl = ref(null);

    // Mother Questions (KB) state
    const motherQuestionsList = ref([]);
    const kbSearchQuery = ref('');
    const selectedKbModule = ref('all');
    const selectedMotherId = ref('G6-FRAC-01');
    const kbModuleFilters = [
      { key: 'all', label: '全部' },
      { key: 'calc', label: '计算' },
      { key: 'fraction', label: '分数应用' },
      { key: 'ratio', label: '比' },
      { key: 'geometry', label: '几何' }
    ];

    // Navigation Switch Helpers
    const setNav = (nav) => {
      currentNav.value = nav;
      if (nav === 'entry' && fastEntryMode.value === 'ocr') {
        renderQrCode();
      }
    };

    const switchFastEntryMode = (mode) => {
      fastEntryMode.value = mode;
      if (mode === 'ocr') {
        renderQrCode();
      }
    };

    // Helper: Module tag to Chinese label
    const getModuleLabel = (mod) => {
      const map = {
        calc: '计算与简算',
        fraction: '分数应用题',
        ratio: '比与按比分配',
        geometry: '圆与几何'
      };
      return map[mod] || mod || '基础数学';
    };

    // Helper: Get student primary weakness
    const getStudentWeakness = (stuId) => {
      const s = overviewData.value.students.find(item => item.id === stuId);
      return (s && s.primary_weakness) ? s.primary_weakness : '分数应用 · 找准单位 1';
    };

    // Auth & Session
    const checkAuth = async () => {
      try {
        const resp = await fetch('/api/auth/check');
        const data = await resp.json();
        if (!data.authenticated) {
          window.location.href = '/login.html';
        }
      } catch (err) {
        window.location.href = '/login.html';
      }
    };

    const logout = async () => {
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } catch (e) {}
      window.location.href = '/login.html';
    };

    // Load Overview Data
    const loadOverview = async () => {
      try {
        const resp = await fetch('/api/students');
        if (resp.status === 401) {
          window.location.href = '/login.html';
          return;
        }
        overviewData.value = await resp.json();
        const stus = overviewData.value.students;
        if (stus.length > 0) {
          if (!currentStudentId.value) currentStudentId.value = stus[0].id;
          if (!ocrStudentId.value) ocrStudentId.value = stus[0].id;
          if (!trackingStudentId.value) {
            trackingStudentId.value = stus[0].id;
            loadStudentDetail();
          }
          if (selectedStudentIds.value.length === 0) {
            selectedStudentIds.value = stus.map(s => s.id);
          }
        }
      } catch (err) {
        console.error('Failed to load overview:', err);
      }
    };

    // Computed Properties for Home & Sheets
    const pendingSheetsList = computed(() => {
      return sheetsList.value.filter(s => s.status !== 'checked');
    });

    const pendingSheetsCount = computed(() => {
      return pendingSheetsList.value.length;
    });

    const latestBatch = computed(() => {
      if (sheetsList.value.length === 0) return null;
      return sheetsList.value[0];
    });

    const latestBatchSheets = computed(() => {
      if (!latestBatch.value) return [];
      const code = latestBatch.value.batch_code;
      return sheetsList.value.filter(s => s.batch_code === code);
    });

    const historicalBatches = computed(() => {
      if (!latestBatch.value) return [];
      const latestCode = latestBatch.value.batch_code;
      const groups = {};
      sheetsList.value.forEach(s => {
        if (s.batch_code !== latestCode) {
          if (!groups[s.batch_code]) {
            groups[s.batch_code] = {
              batch_code: s.batch_code,
              pdf_path: s.pdf_path,
              sheets: []
            };
          }
          groups[s.batch_code].sheets.push(s);
        }
      });
      return Object.values(groups).map(g => ({
        batch_code: g.batch_code,
        pdf_path: g.pdf_path,
        count: g.sheets.length,
        is_all_checked: g.sheets.every(s => s.status === 'checked')
      }));
    });

    const currentStudentIndexDisplay = computed(() => {
      const idx = overviewData.value.students.findIndex(s => s.id === currentStudentId.value);
      return String(idx >= 0 ? idx + 1 : 1).padStart(2, '0');
    });

    const isAllSelected = computed(() => {
      return overviewData.value.students.length > 0 &&
        selectedStudentIds.value.length === overviewData.value.students.length;
    });

    const toggleSelectAll = () => {
      if (isAllSelected.value) {
        selectedStudentIds.value = [];
      } else {
        selectedStudentIds.value = overviewData.value.students.map(s => s.id);
      }
    };

    // Fast Entry Tab A Actions
    const loadExams = async () => {
      try {
        const resp = await fetch('/api/exams');
        examList.value = await resp.json();
        if (examList.value.length > 0 && !selectedExamId.value) {
          selectedExamId.value = examList.value[0].id;
          await loadExamQuestions();
        }
      } catch (err) {
        console.error('Failed to load exams:', err);
      }
    };

    const loadExamQuestions = async () => {
      if (!selectedExamId.value) return;
      try {
        const resp = await fetch(`/api/exams/${selectedExamId.value}`);
        const data = await resp.json();
        examQuestions.value = data.questions || [];
      } catch (err) {
        console.error('Failed to load exam questions:', err);
      }
    };

    const isQuestionTicked = (qId) => {
      return tickedMistakes.value.some(m => m.exam_question_id === qId);
    };

    const toggleTickQuestion = (q) => {
      const idx = tickedMistakes.value.findIndex(m => m.exam_question_id === q.id);
      if (idx >= 0) {
        tickedMistakes.value.splice(idx, 1);
      } else {
        const defaultTags = q.quick_error_tags && q.quick_error_tags.length > 0 ? [q.quick_error_tags[0]] : ['计算粗心'];
        tickedMistakes.value.push({
          q_no: q.q_no,
          exam_question_id: q.id,
          kb_mother_id: q.kb_mother_id,
          module_tag: q.module_tag,
          error_tags: defaultTags
        });
      }
    };

    const getQuickTags = (qId) => {
      const q = examQuestions.value.find(item => item.id === qId);
      return (q && q.quick_error_tags && q.quick_error_tags.length > 0)
        ? q.quick_error_tags
        : ['找错单位1', '乘法分配律漏乘', '数量关系不清', '计算粗心'];
    };

    const toggleMistakeTag = (tm, tag) => {
      const pos = tm.error_tags.indexOf(tag);
      if (pos >= 0) {
        tm.error_tags.splice(pos, 1);
      } else {
        tm.error_tags.push(tag);
      }
    };

    const submitBatchTick = async () => {
      if (isSubmitting.value) return;
      if (!selectedExamId.value || !currentStudentId.value) {
        alert('请选择试卷和学生');
        return;
      }

      isSubmitting.value = true;
      try {
        const resp = await fetch('/api/exams/batch-tick', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            paper_id: selectedExamId.value,
            student_id: currentStudentId.value,
            score: tickScore.value,
            mistakes: tickedMistakes.value
          })
        });
        const data = await resp.json();
        if (resp.ok && data.success) {
          // Advance to next student in sequence
          const stus = overviewData.value.students;
          const currIdx = stus.findIndex(s => s.id === currentStudentId.value);
          if (currIdx >= 0 && currIdx < stus.length - 1) {
            currentStudentId.value = stus[currIdx + 1].id;
          }
          tickedMistakes.value = [];
          await loadOverview();
        } else {
          alert('保存失败: ' + (data.detail || '未知错误'));
        }
      } catch (err) {
        alert('网络请求失败，输入内容已保留');
      } finally {
        isSubmitting.value = false;
      }
    };

    // Fast Entry Tab B Actions (OCR)
    const triggerOcrFileInput = () => {
      const el = document.getElementById('ocrFileInput');
      if (el) el.click();
    };

    const handleOcrFileUpload = async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      isOcrLoading.value = true;
      ocrParsedResults.value = [];

      const formData = new FormData();
      formData.append('file', file);

      try {
        const resp = await fetch('/api/ocr/upload', {
          method: 'POST',
          body: formData
        });
        const data = await resp.json();
        if (resp.ok) {
          ocrParsedResults.value = data;
        } else {
          alert('OCR 识别失败: ' + (data.detail || '未知错误'));
        }
      } catch (err) {
        alert('网络请求失败');
      } finally {
        isOcrLoading.value = false;
      }
    };

    const getOcrWrongCount = () => {
      return ocrParsedResults.value.filter(item => item.is_wrong).length;
    };

    const submitOcrConfirm = async () => {
      if (isSubmitting.value) return;
      if (!ocrStudentId.value) {
        alert('请选择归属学生');
        return;
      }
      const wrongItems = ocrParsedResults.value.filter(item => item.is_wrong);
      if (wrongItems.length === 0) {
        alert('当前没有选中的错题');
        return;
      }

      isSubmitting.value = true;
      const payload = {
        student_id: ocrStudentId.value,
        items: ocrParsedResults.value.map(item => ({
          kb_mother_id: item.kb_mother_id,
          module_tag: item.module_tag,
          question_snapshot: item.question_text,
          error_tags: item.error_tags,
          photo_url: item.crop_url,
          ocr_note: item.student_answer,
          face_to_face_script: item.face_to_face_script,
          is_wrong: item.is_wrong
        }))
      };

      try {
        const resp = await fetch('/api/ocr/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (resp.ok && data.success) {
          alert(`录入成功！已将 ${data.inserted_count} 道错题存入该生档案。`);
          ocrParsedResults.value = [];
          await loadOverview();
          currentNav.value = 'students';
          trackingStudentId.value = ocrStudentId.value;
          await loadStudentDetail();
        } else {
          alert('确认录入失败: ' + (data.detail || '未知错误'));
        }
      } catch (err) {
        alert('网络请求失败');
      } finally {
        isSubmitting.value = false;
      }
    };

    const checkMobileUpload = async () => {
      try {
        const resp = await fetch('/api/ocr/latest-mobile');
        const data = await resp.json();
        if (data.has_new && data.data) {
          ocrParsedResults.value = data.data.parsed_items || [];
          alert(`已同步到手机最新上传的 ${ocrParsedResults.value.length} 道题目！`);
        } else {
          alert('暂无手机端最新直传记录');
        }
      } catch (err) {
        alert('查询手机上传失败');
      }
    };

    const viewOriginalImage = (url) => {
      previewImageUrl.value = url;
    };

    // Tracking Actions
    const openStudentTracking = (stuId) => {
      trackingStudentId.value = stuId;
      currentNav.value = 'students';
      loadStudentDetail();
    };

    const loadStudentDetail = async () => {
      if (!trackingStudentId.value) return;
      try {
        const resp = await fetch(`/api/students/${trackingStudentId.value}`);
        studentDetail.value = await resp.json();
      } catch (err) {
        console.error('Failed to load student detail:', err);
      }
    };

    const calcScoreDiffText = () => {
      if (!studentDetail.value || !studentDetail.value.student) return '';
      const base = studentDetail.value.student.baseline_score || 50;
      const curr = studentDetail.value.student.current_est_score || 50;
      const diff = Math.round((curr - base) * 10) / 10;
      if (diff > 0) return `比基线提高 ${diff}分`;
      if (diff < 0) return `比基线低 ${Math.abs(diff)}分`;
      return '与基线分持平';
    };

    const getUnmasteredCount = () => {
      if (!studentDetail.value || !studentDetail.value.mistakes) return 0;
      return studentDetail.value.mistakes.filter(m => m.mastery_status !== 'passed').length;
    };

    const saveStudentSummary = async () => {
      if (!studentDetail.value || !studentDetail.value.student) return;
      try {
        const resp = await fetch(`/api/students/${studentDetail.value.student.id}/summary`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ l3_summary: studentDetail.value.student.l3_summary_override || '' })
        });
        const data = await resp.json();
        if (resp.ok) {
          alert('学情备注已保存');
          await loadOverview();
        }
      } catch (err) {
        alert('保存学情失败');
      }
    };

    const generateWechatReport = async () => {
      if (!studentDetail.value || !studentDetail.value.student) return;
      try {
        const resp = await fetch(`/api/students/${studentDetail.value.student.id}/wechat-report`, {
          method: 'POST'
        });
        const data = await resp.json();
        if (resp.ok) {
          wechatData.value = data;
          showWechatModal.value = true;
        } else {
          alert('生成家长周报失败');
        }
      } catch (err) {
        alert('网络请求失败');
      }
    };

    const copyWechatMsg = () => {
      const text = wechatData.value.wechat_msg;
      navigator.clipboard.writeText(text).then(() => {
        alert('微信周报话术已复制到剪贴板！');
      }).catch(() => {
        alert('复制失败，请手动选取文字复制');
      });
    };

    const markMistakePassed = async (mId) => {
      try {
        const resp = await fetch(`/api/students/mistakes/${mId}/pass`, { method: 'POST' });
        if (resp.ok) {
          await loadStudentDetail();
          await loadOverview();
        }
      } catch (err) {
        alert('操作失败');
      }
    };

    const viewMistakeDetail = (m) => {
      if (m.photo_url) {
        previewImageUrl.value = m.photo_url;
      } else if (m.question_snapshot) {
        alert('题目快照:\n' + m.question_snapshot);
      }
    };

    // Sheets & Monday Check Actions
    const loadSheets = async () => {
      try {
        const resp = await fetch('/api/sheets');
        sheetsList.value = await resp.json();
      } catch (err) {
        console.error('Failed to load sheets:', err);
      }
    };

    const openNewSheetModal = () => {
      if (overviewData.value.students.length > 0 && selectedStudentIds.value.length === 0) {
        selectedStudentIds.value = overviewData.value.students.map(s => s.id);
      }
      showNewSheetModal.value = true;
    };

    const submitGenerateSheets = async () => {
      if (isSubmitting.value) return;
      if (selectedStudentIds.value.length === 0) return;

      isSubmitting.value = true;
      try {
        const resp = await fetch('/api/sheets/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_ids: selectedStudentIds.value,
            batch_title: newBatchTitle.value
          })
        });
        const data = await resp.json();
        if (resp.ok && data.success) {
          showNewSheetModal.value = false;
          await loadSheets();
          await loadOverview();
          alert(`巩固单生成成功！已生成合并 A4 PDF（共 ${data.student_count + 1} 页，含末页教师速查答案）。`);
          if (data.pdf_path) {
            window.open(data.pdf_path, '_blank');
          }
        } else {
          alert('生成失败: ' + (data.detail || '未知错误'));
        }
      } catch (err) {
        alert('网络请求失败');
      } finally {
        isSubmitting.value = false;
      }
    };

    const downloadPdf = (url) => {
      if (url) {
        window.open(url, '_blank');
      } else {
        alert('PDF 文件生成中或不存在');
      }
    };

    const openMondayCheckModal = async (sheetId) => {
      try {
        const resp = await fetch(`/api/sheets/${sheetId}`);
        currentSheetDetail.value = await resp.json();

        // Reverse check: DEFAULT all items CHECKED (passed)
        mondayCheckStates.value = {};
        for (const item of currentSheetDetail.value.items) {
          mondayCheckStates.value[item.id] = true;
        }
        showMondayModal.value = true;
      } catch (err) {
        alert('加载巩固单明细失败');
      }
    };

    const toggleMondayCheck = (itemId) => {
      mondayCheckStates.value[itemId] = !mondayCheckStates.value[itemId];
    };

    const mondayPassedCount = computed(() => {
      if (!currentSheetDetail.value || !currentSheetDetail.value.items) return 0;
      return currentSheetDetail.value.items.filter(item => !!mondayCheckStates.value[item.id]).length;
    });

    const mondayFailedCount = computed(() => {
      if (!currentSheetDetail.value || !currentSheetDetail.value.items) return 0;
      return currentSheetDetail.value.items.filter(item => !mondayCheckStates.value[item.id]).length;
    });

    const getItemTypeLabel = (type) => {
      if (type === 'calc_warmup' || type === 'warmup') return '计算热身';
      if (type === 'mistake_redo' || type === 'retry') return '错题重练';
      if (type === 'variant') return '同源变式';
      return '基础练习';
    };

    const getItemTitle = (item) => {
      if (item.sort_order === 1) return '分数乘法简算';
      if (item.sort_order === 2) return '乘法分配律应用';
      if (item.sort_order === 3) return '解简易方程';
      if (item.kb_mother_id === 'G6-FRAC-01') return '找准单位 1 求部分量与总量';
      if (item.kb_mother_id === 'G6-RATIO-01') return '按比分配应用';
      if (item.kb_mother_id === 'G6-GEO-01') return '圆的周长与面积应用';
      if (item.kb_mother_id === 'G6-CALC-01') return '分数四则混合运算与简算';
      if (item.question_md) {
        const clean = item.question_md.replace(/[$#*_\\]/g, '').replace(/frac\{(\d+)\}\{(\d+)\}/g, '$1/$2').trim();
        return clean.length > 20 ? clean.slice(0, 20) + '...' : clean;
      }
      return '重点知识巩固';
    };

    const submitMondayCheck = async () => {
      if (isSubmitting.value) return;
      if (!currentSheetDetail.value) return;

      isSubmitting.value = true;
      const checks = Object.keys(mondayCheckStates.value).map(k => ({
        item_id: parseInt(k),
        passed: !!mondayCheckStates.value[k]
      }));

      try {
        const resp = await fetch('/api/sheets/monday-check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sheet_id: currentSheetDetail.value.id,
            checks: checks
          })
        });
        const data = await resp.json();
        if (resp.ok && data.success) {
          alert(data.message);
          showMondayModal.value = false;
          await loadSheets();
          await loadOverview();
          if (trackingStudentId.value === currentSheetDetail.value.student_id) {
            await loadStudentDetail();
          }
        } else {
          alert('验收失败: ' + (data.detail || '未知错误'));
        }
      } catch (err) {
        alert('网络请求失败');
      } finally {
        isSubmitting.value = false;
      }
    };

    // Mother Questions (KB) Actions
    const loadMotherQuestions = async () => {
      try {
        const resp = await fetch('/api/kb/mothers');
        motherQuestionsList.value = await resp.json();
        if (motherQuestionsList.value.length > 0 && !selectedMotherId.value) {
          selectedMotherId.value = motherQuestionsList.value[0].id;
        }
      } catch (err) {
        console.error('Failed to load mother questions:', err);
      }
    };

    const filteredMotherQuestions = computed(() => {
      let list = motherQuestionsList.value;
      if (selectedKbModule.value !== 'all') {
        list = list.filter(item => item.module_tag === selectedKbModule.value);
      }
      if (kbSearchQuery.value.trim()) {
        const q = kbSearchQuery.value.toLowerCase().trim();
        list = list.filter(item =>
          item.title?.toLowerCase().includes(q) ||
          item.id?.toLowerCase().includes(q) ||
          item.tags?.some(t => t.toLowerCase().includes(q)) ||
          item.full_body?.toLowerCase().includes(q)
        );
      }
      return list;
    });

    const currentSelectedMother = computed(() => {
      return motherQuestionsList.value.find(m => m.id === selectedMotherId.value) || motherQuestionsList.value[0] || null;
    });

    const getSectionContent = (mother, secKey) => {
      if (!mother) return '';
      if (mother.sections && mother.sections[secKey]) {
        return mother.sections[secKey].trim();
      }
      return '';
    };

    // QR Code Render Helper
    const renderQrCode = () => {
      setTimeout(() => {
        const qrEl = document.getElementById('qrcode');
        if (qrEl) {
          qrEl.innerHTML = '';
          const uploadUrl = window.location.origin + '/upload.html';
          new QRCode(qrEl, {
            text: uploadUrl,
            width: 104,
            height: 104,
            colorDark: '#23322B',
            colorLight: '#ffffff',
            correctLevel: QRCode.CorrectLevel.M
          });
        }
      }, 50);
    };

    // Mount
    onMounted(async () => {
      await checkAuth();
      await loadOverview();
      await loadExams();
      await loadSheets();
      await loadMotherQuestions();
      renderQrCode();
    });

    return {
      currentNav,
      fastEntryMode,
      setNav,
      switchFastEntryMode,
      isSubmitting,
      currentDateStr,
      overviewData,
      pendingSheetsList,
      pendingSheetsCount,
      latestBatch,
      latestBatchSheets,
      historicalBatches,
      currentStudentIndexDisplay,
      isAllSelected,
      toggleSelectAll,
      getModuleLabel,
      getStudentWeakness,
      logout,
      openStudentTracking,
      examList,
      selectedExamId,
      examQuestions,
      currentStudentId,
      tickScore,
      tickedMistakes,
      loadExamQuestions,
      isQuestionTicked,
      toggleTickQuestion,
      getQuickTags,
      toggleMistakeTag,
      submitBatchTick,
      triggerOcrFileInput,
      handleOcrFileUpload,
      isOcrLoading,
      ocrParsedResults,
      ocrStudentId,
      getOcrWrongCount,
      submitOcrConfirm,
      checkMobileUpload,
      viewOriginalImage,
      trackingStudentId,
      studentDetail,
      loadStudentDetail,
      calcScoreDiffText,
      getUnmasteredCount,
      saveStudentSummary,
      generateWechatReport,
      copyWechatMsg,
      markMistakePassed,
      viewMistakeDetail,
      showWechatModal,
      wechatData,
      sheetsList,
      openNewSheetModal,
      showNewSheetModal,
      newBatchTitle,
      selectedStudentIds,
      submitGenerateSheets,
      downloadPdf,
      openMondayCheckModal,
      showMondayModal,
      currentSheetDetail,
      mondayCheckStates,
      toggleMondayCheck,
      mondayPassedCount,
      mondayFailedCount,
      getItemTypeLabel,
      getItemTitle,
      submitMondayCheck,
      motherQuestionsList,
      kbSearchQuery,
      selectedKbModule,
      selectedMotherId,
      kbModuleFilters,
      filteredMotherQuestions,
      currentSelectedMother,
      getSectionContent,
      previewImageUrl
    };
  }
}).mount('#app');
