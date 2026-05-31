// Gallery Management - Main gallery interface and functionality

/**
 * Gallery class handles the main gallery view, search, sorting, and pagination
 */
class Gallery {
    constructor(app = null) {
        this.app = app; // Reference to main app for callbacks
        this.posts = [];  // Current page of posts
        this.totalCount = 0;  // Total count from server for pagination
        this.currentPage = 1;
        this.itemsPerPage = 48;
        this.sortBy = 'post_date';
        this.sortOrder = 'desc';
        this.filterBy = 'all';
        this.searchQuery = '';
        this.showHidden = false;
        this.isLoading = false;
        this.editMode = false;
        
        // DOM elements
        this.galleryGrid = null;
        this.pagination = null;
        this.searchInput = null;
        this.sortSelect = null;
        this.sortOrderBtn = null;
        this.itemsPerPageSelect = null;
        this.editModeBtn = null;
        this.showHiddenBtn = null;
        this.loadingSpinner = null;
        this.paginationInfo = null;

        // State
        this.selectedPosts = new Set();
        this.globalExtractionPollInterval = null;
        this.activeActivePostIds = new Set();
        
        this.init();
    }

    init() {
        this.bindElements();
        this.bindEvents();
        this.loadPreferences();
        this.loadPosts();
    }

    bindElements() {
        this.galleryGrid = document.getElementById('galleryGrid');
        this.pagination = document.getElementById('pagination');
        this.searchInput = document.getElementById('searchInput');
        this.sortSelect = document.getElementById('sortBy');
        this.filterSelect = document.getElementById('filterBy');
        this.sortOrderBtn = document.getElementById('sortOrder');
        this.itemsPerPageSelect = document.getElementById('itemsPerPage');
        this.editModeBtn = document.getElementById('editModeBtn');
        this.loadingSpinner = document.getElementById('loadingSpinner');
        this.paginationInfo = document.getElementById('paginationInfo');
    }

    bindEvents() {
        // Search
        this.searchInput.addEventListener('input', 
            Utils.debounce((e) => this.handleSearch(e.target.value), 300)
        );

        // Sorting
        this.sortSelect.addEventListener('change', (e) => this.handleSortChange(e.target.value));
        this.sortOrderBtn.addEventListener('click', () => this.toggleSortOrder());

        // Filtering
        this.filterSelect.addEventListener('change', (e) => this.handleFilterChange(e.target.value));

        // Pagination
        this.itemsPerPageSelect.addEventListener('change', (e) => {
            this.setItemsPerPage(parseInt(e.target.value));
        });

        // Controls
        this.editModeBtn.addEventListener('click', () => this.toggleEditMode());

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => this.handleKeyboard(e));

        // Handle browser back/forward
        window.addEventListener('popstate', () => this.loadStateFromURL());
    }

    loadPreferences() {
        const prefs = Utils.storage.get('gallery_preferences', {});
        
        if (prefs.itemsPerPage) {
            this.itemsPerPage = prefs.itemsPerPage;
            this.itemsPerPageSelect.value = this.itemsPerPage;
        }
        
        if (prefs.sortBy) {
            this.sortBy = prefs.sortBy;
            this.sortSelect.value = this.sortBy;
        }
        
        if (prefs.sortOrder) {
            this.sortOrder = prefs.sortOrder;
            this.updateSortOrderIcon();
        }
        
        if (prefs.filterBy) {
            this.filterBy = prefs.filterBy;
            this.filterSelect.value = this.filterBy;
        }

        // Load state from URL
        this.loadStateFromURL();
    }

    savePreferences() {
        Utils.storage.set('gallery_preferences', {
            itemsPerPage: this.itemsPerPage,
            sortBy: this.sortBy,
            sortOrder: this.sortOrder,
            filterBy: this.filterBy
        });
    }

    loadStateFromURL() {
        const params = Utils.url.getParams();
        
        if (params.page) this.currentPage = parseInt(params.page) || 1;
        if (params.search) {
            this.searchQuery = params.search;
            this.searchInput.value = this.searchQuery;
        }
        if (params.sort) this.sortBy = params.sort;
        if (params.order) this.sortOrder = params.order;
        if (params.filter) this.filterBy = params.filter;
    }

    updateURL() {
        const params = {};
        
        if (this.currentPage > 1) params.page = this.currentPage;
        if (this.searchQuery) params.search = this.searchQuery;
        if (this.sortBy !== 'post_date') params.sort = this.sortBy;
        if (this.sortOrder !== 'desc') params.order = this.sortOrder;
        if (this.filterBy !== 'all') params.filter = this.filterBy;
        if (this.showHidden) params.hidden = 'true';

        const queryString = new URLSearchParams(params).toString();
        const newUrl = window.location.pathname + (queryString ? '?' + queryString : '');
        
        window.history.replaceState({}, '', newUrl);
    }

    async loadPosts() {
        if (this.isLoading) return;
        
        this.setLoading(true);
        
        try {
            // Build API parameters with current filters, sorting, and pagination
            const params = {
                page: this.currentPage,
                per_page: this.itemsPerPage,
                show_hidden: this.editMode  // Show hidden posts only in edit mode
            };
            
            // Add search query if present
            if (this.searchQuery) {
                params.search = this.searchQuery;
            }
            
            // Add filter parameter
            if (this.filterBy && this.filterBy !== 'all') {
                params.filter = this.filterBy;
            }
            
            // Add sort parameters
            params.sort_by = this.sortBy;
            params.sort_order = this.sortOrder;
            
            const result = await API.metadata.getPosts(params);
            
            if (result.success) {
                const responseData = result.data;
                
                // Server returns paginated response
                this.posts = responseData.posts || [];
                
                // Get total count from server pagination info
                if (responseData.pagination) {
                    this.totalCount = responseData.pagination.total;
                } else {
                    this.totalCount = this.posts.length;
                }
                
                // Filter: only show posts with ZIP files and profile images (client-side safety check)
                this.posts = this.posts.filter(post => {
                    const hasZipFiles = post.zip_files && post.zip_files.length > 0;
                    const hasProfileImages = post.profile_images && post.profile_images.length > 0;
                    return hasZipFiles && hasProfileImages;
                });
                
                this.render();
                await this.rehydrateExtractionStates();
            } else {
                throw new Error(result.error || 'Failed to load posts');
            }
        } catch (error) {
            console.error('Failed to load posts:', error);
            this.showStatus('Failed to load posts: ' + error.message, 'error');
            this.renderError();
        } finally {
            this.setLoading(false);
        }
    }

    async filterAndSort() {
        // Reset to page 1 when filters/sort change
        this.currentPage = 1;
        
        // Re-fetch from server with new parameters
        await this.loadPosts();
    }

    render() {
        this.renderGallery();
        this.renderPagination();
        this.updatePaginationInfo();
        this.updateURL();
    }

    async rehydrateExtractionStates() {
        try {
            const result = await API.get('/posts/extract/active', {}, false);
            if (!result.success || !result.data || !Array.isArray(result.data.jobs)) {
                return;
            }

            const activeJobsByPostId = new Map(result.data.jobs.map(job => [job.post_id, job]));
            this.activeActivePostIds = new Set(
                result.data.jobs
                    .filter(job => job.status === 'queued' || job.status === 'running')
                    .map(job => job.post_id)
            );

            if (this.activeActivePostIds.size > 0) {
                this.startGlobalExtractionPolling();
            } else {
                this.stopGlobalExtractionPolling();
            }

            this.posts.forEach(post => {
                const activeJob = activeJobsByPostId.get(post.post_id);
                if (!activeJob) {
                    this.setExtractionLoading(post, false);
                    return;
                }

                this.setExtractionLoading(post, true, activeJob.status === 'queued' ? 'queued' : 'extract');

                const progress = activeJob.progress || {
                    step: activeJob.status,
                    message: activeJob.status === 'queued' ? 'Queued for extraction...' : 'Starting extraction...',
                    percent: activeJob.status === 'queued' ? 0 : 1
                };

                this.updateExtractionProgress(post.post_id, progress, activeJob.status);
            });
        } catch (error) {
            console.warn('Failed to rehydrate extraction state:', error);
        }
    }

    startGlobalExtractionPolling() {
        if (this.globalExtractionPollInterval) return;

        this.pollGlobalExtractionStatus();
        this.globalExtractionPollInterval = setInterval(() => {
            this.pollGlobalExtractionStatus();
        }, 5000);
    }

    stopGlobalExtractionPolling() {
        if (this.globalExtractionPollInterval) {
            clearInterval(this.globalExtractionPollInterval);
            this.globalExtractionPollInterval = null;
        }
    }

    async pollGlobalExtractionStatus() {
        try {
            const response = await API.get('/posts/extract/active', {}, false);
            if (!response.success || !response.data || !Array.isArray(response.data.jobs)) {
                return;
            }

            const activeJobs = response.data.jobs;
            const activeByPostId = new Map(activeJobs.map(job => [job.post_id, job]));
            const currentActivePostIds = new Set(
                activeJobs
                    .filter(job => job.status === 'queued' || job.status === 'running')
                    .map(job => job.post_id)
            );

            for (const post of this.posts) {
                const activeJob = activeByPostId.get(post.post_id);
                if (!activeJob) {
                    this.setExtractionLoading(post, false);
                    continue;
                }

                this.setExtractionLoading(post, true, activeJob.status === 'queued' ? 'queued' : 'extract');
                this.updateExtractionProgress(
                    post.post_id,
                    activeJob.progress || {
                        step: activeJob.status,
                        message: activeJob.status === 'queued' ? 'Queued for extraction...' : 'Extracting files...',
                        percent: activeJob.status === 'queued' ? 0 : 1
                    },
                    activeJob.status
                );
            }

            const finishedPostIds = [...this.activeActivePostIds].filter(
                postId => !currentActivePostIds.has(postId)
            );

            for (const postId of finishedPostIds) {
                await this.checkAndHandleExtractionCompletion(postId);
            }

            this.activeActivePostIds = currentActivePostIds;

            if (this.activeActivePostIds.size === 0) {
                this.stopGlobalExtractionPolling();
            }
        } catch (error) {
            console.warn('Global extraction polling failed:', error);
        }
    }

    async checkAndHandleExtractionCompletion(postId) {
        try {
            const response = await API.get(`/posts/${postId}/extract/progress`, {}, false);
            if (response.success && response.data && response.data.completed) {
                await this.handleExtractionComplete(postId, response.data);
            }
        } catch (error) {
            console.warn(`Failed completion check for post ${postId}:`, error);
        }
    }

    renderGallery() {
        if (!this.galleryGrid) return;
        
        this.galleryGrid.innerHTML = '';
        
        if (this.posts.length === 0) {
            this.renderEmptyState();
            return;
        }
        
        // Server already sent us the exact posts for this page
        this.posts.forEach(post => {
            const item = this.createGalleryItem(post);
            this.galleryGrid.appendChild(item);
        });
        
        // Add animation
        this.galleryGrid.classList.add('fade-in');
    }

    createGalleryItem(post) {
        const item = Utils.dom.createElement('div', 'gallery-item');
        
        // Apply hidden styling (matching cascade_template.html lines 404-413)
        if (post.display === false) {
            item.classList.add('hidden-post');
            // In edit mode, FORCE visibility (matching cascade behavior)
            if (this.editMode) {
                item.style.display = 'block';
                item.style.visibility = 'visible';
            }
        }
        
        if (this.editMode) {
            item.classList.add('edit-mode');
        }

        const imageContainer = Utils.dom.createElement('div', 'gallery-image-container');
        
        // Profile image
        const profileImage = post.profile_images && post.profile_images.length > 0 
            ? post.profile_images[0] 
            : null;
            
        if (profileImage) {
            const img = Utils.dom.createElement('img', 'gallery-image', {
                src: API.images.getProfileImageUrl(post.post_id),
                alt: post.revised_post_name,
                loading: 'lazy'
            });
            
            img.addEventListener('error', () => {
                img.style.display = 'none';
                const placeholder = Utils.dom.createElement('div', 'image-placeholder', {
                    innerHTML: '<i class="fas fa-image"></i>'
                });
                imageContainer.appendChild(placeholder);
            });
            
            imageContainer.appendChild(img);
        } else {
            const placeholder = Utils.dom.createElement('div', 'image-placeholder', {
                innerHTML: '<i class="fas fa-image"></i>'
            });
            imageContainer.appendChild(placeholder);
        }
        
        // Hidden indicator
        const hiddenIndicator = Utils.dom.createElement('div', 'hidden-indicator', {
            innerHTML: '<i class="fas fa-eye-slash"></i> Hidden'
        });
        imageContainer.appendChild(hiddenIndicator);
        
        // Extraction progress overlay
        const extractionProgress = Utils.dom.createElement('div', 'extraction-progress');
        const progressContainer = Utils.dom.createElement('div', 'progress-container');
        
        const progressCircle = Utils.dom.createElement('div', 'progress-circle');
        const progressIcon = Utils.dom.createElement('i', 'progress-icon fas fa-cog');
        progressCircle.appendChild(progressIcon);
        
        const progressText = Utils.dom.createElement('div', 'progress-text', {
            textContent: 'Extracting...'
        });
        const progressDetails = Utils.dom.createElement('div', 'progress-details', {
            textContent: 'Please wait'
        });
        
        progressContainer.appendChild(progressCircle);
        progressContainer.appendChild(progressText);
        progressContainer.appendChild(progressDetails);
        extractionProgress.appendChild(progressContainer);
        imageContainer.appendChild(extractionProgress);
        
        // Edit overlay - add inside image container so it doesn't overlap content
        const editOverlay = Utils.dom.createElement('div', 'edit-overlay');
        const editControls = this.createEditControls(post);
        editOverlay.appendChild(editControls);
        imageContainer.appendChild(editOverlay);
        
        // Content
        const content = Utils.dom.createElement('div', 'gallery-content');
        
        const title = Utils.dom.createElement('h3', 'gallery-title', {
            textContent: post.revised_post_name
        });
        content.appendChild(title);
        
        const meta = Utils.dom.createElement('div', 'gallery-meta');
        
        // Post date
        const dateRow = Utils.dom.createElement('div', 'meta-row');
        dateRow.innerHTML = `
            <span class="meta-label">Posted:</span>
            <span class="meta-value post-date">${Utils.formatDate(post.post_date)}</span>
        `;
        meta.appendChild(dateRow);
        
        // File count and status
        if (post.zip_files && post.zip_files.length > 0) {
            const filesRow = Utils.dom.createElement('div', 'meta-row');
            const extracted = post.zip_files.some(zip => zip.extracted);
            const statusClass = extracted ? 'extracted' : 'not-extracted';
            const statusText = extracted ? 'Extracted' : 'Not Extracted';
            const statusIcon = extracted ? 'fa-check' : 'fa-archive';

            const isFavourite = post.favourite === true;
            const heartIcon = isFavourite ? 'fas fa-heart' : 'far fa-heart';
            const heartClass = isFavourite ? 'favourite-btn active' : 'favourite-btn';

            filesRow.innerHTML = `
                <span class="meta-label">${post.zip_files.length} file(s):</span>
                <button class="${heartClass}" data-post-id="${post.post_id}" title="${isFavourite ? 'Remove from favourites' : 'Add to favourites'}">
                    <i class="${heartIcon}"></i>
                </button>
                <span class="zip-status ${statusClass}">
                    <i class="fas ${statusIcon}"></i> ${statusText}
                </span>
            `;

            const favouriteBtn = filesRow.querySelector('.favourite-btn');
            favouriteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleFavourite(post, favouriteBtn);
            });

            meta.appendChild(filesRow);
        }
        
        content.appendChild(meta);
        
        // Rename container
        const renameContainer = this.createRenameContainer(post);
        
        // Assemble item
        item.appendChild(imageContainer);
        item.appendChild(content);
        item.appendChild(renameContainer);
        
        // Event listeners
        item.addEventListener('click', (e) => {
            // Always prevent edit mode clicks
            if (this.editMode) {
                e.stopPropagation();
                return;
            }
            
            // Check if the click is on excluded elements
            const clickedElement = e.target;
            const isExcludedClick = clickedElement.closest('.extraction-progress') ||
                                  clickedElement.closest('.edit-overlay') ||
                                  clickedElement.closest('.edit-controls') ||
                                  clickedElement.closest('.rename-container');
            
            if (isExcludedClick) {
                e.stopPropagation();
                return;
            }
            
            // Allow clicks on the image container or image itself
            const isValidClick = clickedElement.closest('.gallery-image-container') ||
                               clickedElement.closest('.gallery-content') ||
                               clickedElement === item;
            
            if (isValidClick) {
                console.log('Valid click detected on post:', post.post_id);
                this.openPost(post);
            }
        });
        
        item.dataset.postId = post.post_id;
        
        return item;
    }

    createEditControls(post) {
        const controls = Utils.dom.createElement('div', 'edit-controls');
        
        const header = Utils.dom.createElement('div', 'edit-controls-header');
        const title = Utils.dom.createElement('h4', 'edit-controls-title', {
            textContent: 'Edit Post'
        });
        header.appendChild(title);
        controls.appendChild(header);
        
        // Visibility toggle - matching cascade template logic (lines 543-559)
        // When visible (display=true): show eye-slash icon with "Hide" text
        // When hidden (display=false): show eye icon with "Show" text
        const isVisible = post.display !== false; // treat undefined as visible
        const visibilityBtn = Utils.dom.createElement('button', `edit-btn visibility ${isVisible ? 'hide-state' : 'show-state'}`, {
            innerHTML: `<i class="fas fa-${isVisible ? 'eye-slash' : 'eye'}"></i><span class="btn-text">${isVisible ? 'Hide' : 'Show'}</span>`
        });
        visibilityBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleVisibility(post);
        });
        controls.appendChild(visibilityBtn);
        
        // Extract/Delete toggle
        const hasExtracted = post.zip_files && post.zip_files.some(zip => zip.extracted);
        const extractBtn = Utils.dom.createElement('button', `edit-btn extract ${hasExtracted ? 'extracted' : ''}`, {
            innerHTML: `<i class="fas fa-${hasExtracted ? 'folder-minus' : 'folder-plus'}"></i><span class="btn-text">${hasExtracted ? 'Remove Files' : 'Download Files'}</span>`
        });
        extractBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (hasExtracted) {
                this.deleteExtracted(post);
            } else {
                this.extractFiles(post);
            }
        });
        controls.appendChild(extractBtn);
        
        // Rename
        const renameBtn = Utils.dom.createElement('button', 'edit-btn rename', {
            innerHTML: '<i class="fas fa-edit"></i><span class="btn-text">Rename</span>'
        });
        renameBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.startRename(post);
        });
        controls.appendChild(renameBtn);
        
        return controls;
    }

    createRenameContainer(post) {
        const container = Utils.dom.createElement('div', 'rename-container');
        
        const label = Utils.dom.createElement('label', 'rename-label', {
            textContent: 'Post Name:'
        });
        container.appendChild(label);
        
        const input = Utils.dom.createElement('input', 'rename-input', {
            type: 'text',
            value: post.revised_post_name,
            placeholder: 'Enter new name...'
        });
        container.appendChild(input);
        
        const actions = Utils.dom.createElement('div', 'rename-actions');
        
        const saveBtn = Utils.dom.createElement('button', 'rename-btn rename-save', {
            innerHTML: '<i class="fas fa-check"></i> Save'
        });
        
        const cancelBtn = Utils.dom.createElement('button', 'rename-btn rename-cancel', {
            innerHTML: '<i class="fas fa-times"></i> Cancel'
        });
        
        actions.appendChild(saveBtn);
        actions.appendChild(cancelBtn);
        container.appendChild(actions);
        
        // Event listeners
        saveBtn.addEventListener('click', () => this.saveRename(post, input.value));
        cancelBtn.addEventListener('click', () => this.cancelRename(post));
        
        input.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter') {
                this.saveRename(post, input.value);
            } else if (e.key === 'Escape') {
                this.cancelRename(post);
            }
        });
        
        return container;
    }

    renderEmptyState() {
        const emptyState = Utils.dom.createElement('div', 'empty-state', {
            innerHTML: `
                <i class="fas fa-search"></i>
                <h3>No posts found</h3>
                <p>${this.searchQuery ? 
                    `No posts match "${this.searchQuery}". Try adjusting your search terms.` :
                    'No posts available. Check your filters or refresh the page.'
                }</p>
            `
        });
        this.galleryGrid.appendChild(emptyState);
    }

    renderError() {
        this.galleryGrid.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-exclamation-triangle"></i>
                <h3>Error loading posts</h3>
                <p>There was a problem loading the gallery. Please refresh the page to try again.</p>
            </div>
        `;
    }

    renderPagination() {
        if (!this.pagination) return;
        
        this.pagination.innerHTML = '';
        
        const totalPages = this.getTotalPages();
        if (totalPages <= 1) return;
        
        // Previous button
        const prevBtn = this.createPaginationButton(
            '<i class="fas fa-chevron-left"></i>',
            this.currentPage - 1,
            this.currentPage <= 1
        );
        this.pagination.appendChild(prevBtn);
        
        // Page numbers
        const { start, end } = this.getPaginationRange(this.currentPage, totalPages);
        
        if (start > 1) {
            this.pagination.appendChild(this.createPaginationButton('1', 1));
            if (start > 2) {
                this.pagination.appendChild(this.createPaginationEllipsis());
            }
        }
        
        for (let i = start; i <= end; i++) {
            const btn = this.createPaginationButton(i.toString(), i, false, i === this.currentPage);
            this.pagination.appendChild(btn);
        }
        
        if (end < totalPages) {
            if (end < totalPages - 1) {
                this.pagination.appendChild(this.createPaginationEllipsis());
            }
            this.pagination.appendChild(this.createPaginationButton(totalPages.toString(), totalPages));
        }
        
        // Next button
        const nextBtn = this.createPaginationButton(
            '<i class="fas fa-chevron-right"></i>',
            this.currentPage + 1,
            this.currentPage >= totalPages
        );
        this.pagination.appendChild(nextBtn);
    }

    createPaginationButton(text, page, disabled = false, active = false) {
        const btn = Utils.dom.createElement('button', 'pagination-btn', {
            innerHTML: text,
            disabled: disabled,
            type: 'button'
        });
        
        if (active) btn.classList.add('active');
        
        if (!disabled) {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.goToPage(page);
            });
        }
        
        return btn;
    }

    createPaginationEllipsis() {
        return Utils.dom.createElement('span', 'pagination-ellipsis', {
            textContent: '...'
        });
    }

    getPaginationRange(current, total, delta = 2) {
        const range = {
            start: Math.max(1, current - delta),
            end: Math.min(total, current + delta)
        };
        
        if (range.end - range.start < 2 * delta) {
            if (range.start === 1) {
                range.end = Math.min(total, range.start + 2 * delta);
            } else if (range.end === total) {
                range.start = Math.max(1, range.end - 2 * delta);
            }
        }
        
        return range;
    }

    updatePaginationInfo() {
        if (!this.paginationInfo) return;
        
        const total = this.totalCount;
        const start = Math.min((this.currentPage - 1) * this.itemsPerPage + 1, total);
        const end = Math.min(this.currentPage * this.itemsPerPage, total);
        
        this.paginationInfo.textContent = 
            total > 0 ? `Showing ${start}-${end} of ${total} posts` : 'No posts to display';
    }

    // Event handlers
    async handleSearch(query) {
        this.searchQuery = query.trim();
        await this.filterAndSort();
    }

    async handleSortChange(sortBy) {
        if (this.sortBy !== sortBy) {
            this.sortBy = sortBy;
            await this.filterAndSort();
            this.savePreferences();
        }
    }

    async handleFilterChange(filterBy) {
        if (this.filterBy !== filterBy) {
            this.filterBy = filterBy;
            await this.filterAndSort();
            this.savePreferences();
        }
    }

    async toggleSortOrder() {
        this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
        this.updateSortOrderIcon();
        await this.filterAndSort();
        this.savePreferences();
    }

    updateSortOrderIcon() {
        const icon = this.sortOrderBtn.querySelector('i');
        icon.className = `fas fa-sort-amount-${this.sortOrder === 'asc' ? 'up' : 'down'}`;
    }

    async setItemsPerPage(count) {
        this.itemsPerPage = count;
        this.currentPage = 1;
        await this.loadPosts();
        this.savePreferences();
    }

    async toggleEditMode() {
        this.editMode = !this.editMode;
        this.editModeBtn.classList.toggle('active', this.editMode);
        document.body.classList.toggle('edit-mode-active', this.editMode);
        const pageBeforeToggle = this.currentPage;
        
        console.log('toggleEditMode - Edit mode is now:', this.editMode);
        console.log('toggleEditMode - Body has edit-mode-active?', document.body.classList.contains('edit-mode-active'));
        
        // Refresh playlist cards if in playlist mode (do this BEFORE gallery render)
        const playlistsGrid = document.getElementById('playlistsGrid');
        const isPlaylistVisible = playlistsGrid && window.getComputedStyle(playlistsGrid).display !== 'none';
        
        console.log('toggleEditMode - playlist visible?', isPlaylistVisible);
        console.log('toggleEditMode - this.app exists?', !!this.app);
        console.log('toggleEditMode - condition result:', this.app && isPlaylistVisible);
        
        if (this.app && isPlaylistVisible) {
            console.log('Calling loadPlaylistCards from toggleEditMode');
            await this.app.loadPlaylistCards();
            console.log('loadPlaylistCards completed');
        } else {
            console.log('NOT calling loadPlaylistCards - this.app:', !!this.app, 'isPlaylistVisible:', isPlaylistVisible);
        }
        
        // Re-fetch posts with new show_hidden parameter without resetting pagination
        this.currentPage = pageBeforeToggle;
        await this.loadPosts();
        
        statusManager.showInfo(this.editMode ? 'Edit mode enabled - showing all posts' : 'Edit mode disabled - hidden posts now hidden');
    }

    handleKeyboard(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        
        switch (e.key) {
            case 'ArrowLeft':
                if (this.currentPage > 1) {
                    this.goToPage(this.currentPage - 1);
                }
                break;
            case 'ArrowRight':
                if (this.currentPage < this.getTotalPages()) {
                    this.goToPage(this.currentPage + 1);
                }
                break;
            case 'Home':
                this.goToPage(1);
                break;
            case 'End':
                this.goToPage(this.getTotalPages());
                break;
            case 'e':
                if (e.ctrlKey) {
                    e.preventDefault();
                    this.toggleEditMode();
                }
                break;
            case 'f':
                if (e.ctrlKey) {
                    e.preventDefault();
                    this.searchInput.focus();
                    this.searchInput.select();
                }
                break;
            case 'r':
                if (e.ctrlKey) {
                    e.preventDefault();
                    this.refreshPosts();
                }
                break;
        }
    }

    // Utility methods
    getTotalPages() {
        return Math.ceil(this.totalCount / this.itemsPerPage);
    }

    async goToPage(page) {
        if (page >= 1 && page <= this.getTotalPages() && page !== this.currentPage) {
            this.currentPage = page;
            
            // Fetch new page from server
            await this.loadPosts();
            
            // Scroll to top
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    }

    setLoading(loading) {
        this.isLoading = loading;
        if (this.loadingSpinner) {
            this.loadingSpinner.style.display = loading ? 'flex' : 'none';
        }
        if (this.galleryGrid) {
            this.galleryGrid.style.opacity = loading ? '0.5' : '1';
            this.galleryGrid.style.pointerEvents = loading ? 'none' : 'auto';
        }
    }

    // Post operations
    openPost(post) {
        console.log('🔍 Opening post:', post.post_id);
        console.log('📁 Post data:', {
            zip_files: post.zip_files,
            display: post.display,
            cascade_metadata: post.cascade_metadata
        });
        
        // Check if files are extracted - be more thorough
        const hasZipFiles = post.zip_files && post.zip_files.length > 0;
        const hasExtractedFiles = hasZipFiles && post.zip_files.some(zip => zip.extracted === true);
        
        console.log('✅ Extraction check:', {
            hasZipFiles,
            hasExtractedFiles,
            zipDetails: post.zip_files?.map(z => ({filename: z.filename, extracted: z.extracted}))
        });
        
        if (!hasExtractedFiles) {
            console.warn('❌ Post files not extracted:', post.zip_files);
            this.showStatus('Post files need to be downloaded first. Enable edit mode to download files.', 'warning');
            return;
        }
        
        // Navigate to dedicated HTML page - cascade view is now the default!
        const postUrl = `/posts/${post.post_id}_cascade.html`;
        console.log('🎯 Navigating to dedicated cascade page:', postUrl);
        
        try {
            window.open(postUrl, '_blank', 'noopener');
            console.log('✅ Successfully navigating to post page');
        } catch (error) {
            console.error('❌ Error navigating to post page:', error);
            this.showStatus('Error opening post page: ' + error.message, 'error');
            this.createFallbackImageView(post);
        }
    }
    
    createFallbackImageView(post) {
        // Simple fallback - open first image in new tab/window
        if (post.cascade_metadata && post.cascade_metadata.images && post.cascade_metadata.images.length > 0) {
            const firstImage = post.cascade_metadata.images[0];
            const imageUrl = `/api/images/content/${post.post_id}/${firstImage.filename}`;
            console.log('🔗 Opening fallback image view:', imageUrl);
            window.open(imageUrl, '_blank', 'noopener');
        } else {
            console.warn('❌ No images found in cascade metadata');
            this.showStatus('No images available for this post', 'warning');
        }
    }

    async toggleVisibility(post) {
        const isCurrentlyVisible = post.display !== false;
        const newDisplay = !isCurrentlyVisible;
        const oldDisplay = post.display;
        
        // OPTIMISTIC UPDATE: Update UI immediately for instant feedback
        post.display = newDisplay;
        
        // Update in posts array immediately
        const postIndex = this.posts.findIndex(p => p.post_id === post.post_id);
        if (postIndex !== -1) {
            this.posts[postIndex].display = newDisplay;
        }
        
        // Update DOM element directly (INSTANT) - matching cascade behavior
        const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
        if (item) {
            // In edit mode, ALWAYS keep item visible but apply/remove hidden-post class for styling
            if (newDisplay === false) {
                item.classList.add('hidden-post');
                // Force visibility in edit mode
                if (this.editMode) {
                    item.style.display = 'block';
                    item.style.visibility = 'visible';
                }
            } else {
                item.classList.remove('hidden-post');
                item.style.display = '';
                item.style.visibility = '';
            }
            
            // Update the visibility button
            const visibilityBtn = item.querySelector('.visibility');
            if (visibilityBtn) {
                const icon = visibilityBtn.querySelector('i');
                const text = visibilityBtn.querySelector('.btn-text');
                if (newDisplay === false) {
                    visibilityBtn.className = 'edit-btn visibility show-state';
                    if (icon) icon.className = 'fas fa-eye';
                    if (text) text.textContent = 'Show';
                } else {
                    visibilityBtn.className = 'edit-btn visibility hide-state';
                    if (icon) icon.className = 'fas fa-eye-slash';
                    if (text) text.textContent = 'Hide';
                }
            }
        }
        
        // Now make the API call in the background
        try {
            const result = await API.metadata.updatePost(post.post_id, {
                display: newDisplay
            });
            
            if (!result.success) {
                // API call failed - revert the changes
                console.error('Failed to update visibility on server, reverting');
                post.display = oldDisplay;
                if (postIndex !== -1) {
                    this.posts[postIndex].display = oldDisplay;
                }
                
                // Revert UI
                const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
                if (item) {
                    if (oldDisplay === false) {
                        item.classList.add('hidden-post');
                    } else {
                        item.classList.remove('hidden-post');
                        item.style.display = '';
                        item.style.visibility = '';
                    }
                    
                    // Revert button
                    const visibilityBtn = item.querySelector('.visibility');
                    if (visibilityBtn) {
                        const icon = visibilityBtn.querySelector('i');
                        const text = visibilityBtn.querySelector('.btn-text');
                        if (oldDisplay === false) {
                            visibilityBtn.className = 'edit-btn visibility show-state';
                            if (icon) icon.className = 'fas fa-eye';
                            if (text) text.textContent = 'Show';
                        } else {
                            visibilityBtn.className = 'edit-btn visibility hide-state';
                            if (icon) icon.className = 'fas fa-eye-slash';
                            if (text) text.textContent = 'Hide';
                        }
                    }
                }
                
                this.showStatus('Failed to update visibility: ' + (result.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            // Network error - revert changes
            console.error('Network error updating visibility, reverting:', error);
            post.display = oldDisplay;
            if (postIndex !== -1) {
                this.posts[postIndex].display = oldDisplay;
            }
            
            // Revert UI
            const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
            if (item) {
                if (oldDisplay === false) {
                    item.classList.add('hidden-post');
                } else {
                    item.classList.remove('hidden-post');
                    item.style.display = '';
                    item.style.visibility = '';
                }
            }
            
            this.showStatus('Failed to update visibility: ' + error.message, 'error');
        }
    }

    async extractFiles(post) {
        try {
            this.showStatus('Queueing download...', 'info');
            
            // Try to start extraction
            console.log('Sending POST request to start extraction for:', post.post_id);
            const result = await API.files.download(post.post_id);
            console.log('POST response:', result);
            
            // Check if extraction started successfully
            if (!result.success) {
                console.error('POST failed, result:', result);
                throw new Error(result.error || 'Failed to start extraction');
            }
            
            // Check if already extracted
            if (result.data && result.data.message === "Post already extracted") {
                console.log('Post is already extracted');
                this.setExtractionLoading(post, false);
                this.showStatus('Post is already ready', 'info');
                // Update UI to show extracted state
                if (post.zip_files) {
                    post.zip_files.forEach(zip => zip.extracted = true);
                }
                this.updatePostInGrid(post);
                return;
            }
            
            if (result.data && result.data.status === 'queued') {
                this.setExtractionLoading(post, true, 'queued');
                this.updateExtractionProgress(post.post_id, {
                    step: 'queued',
                    message: 'Queued for download...',
                    percent: 0
                }, 'queued');
                this.showStatus('Download queued. It will start when a worker is available.', 'info');
            }

            this.startGlobalExtractionPolling();
            console.log('Extraction request accepted, global polling will monitor active jobs');
            
            // Extraction is now running in background
            // Progress polling will handle completion
            
        } catch (error) {
            console.error('Failed to start download:', error);
            this.showStatus(`Failed to download files: ${error.message}`, 'error');
            this.setExtractionLoading(post, false);
        }
    }
    
    async handleExtractionComplete(postId, completionData) {
        const post = this.posts.find(p => p.post_id === postId);
        if (!post) return;
        
        this.setExtractionLoading(post, false);
        
        if (completionData.extraction_success) {
            console.log('Extraction completed successfully');
            
            const updatedLocalPost = {
                ...post,
                extracted: true,
                zip_files: Array.isArray(post.zip_files)
                    ? post.zip_files.map(zip => ({
                        ...zip,
                        extracted: true,
                        downloaded: true
                    }))
                    : post.zip_files
            };
            
            const postIndex = this.posts.findIndex(p => p.post_id === postId);
            if (postIndex !== -1) {
                this.posts[postIndex] = { ...this.posts[postIndex], ...updatedLocalPost };
            }
            
            Object.assign(post, updatedLocalPost);
            this.updatePostInGrid(updatedLocalPost);
            this.showStatus('Files downloaded successfully! You can now click the image to view it.', 'success');
            
            // Refresh from server for consistency only
            setTimeout(async () => {
                try {
                    const refreshResult = await API.metadata.getPost(postId);
                    if (refreshResult.success) {
                        const updatedPost = refreshResult.data;
                        Object.assign(post, updatedPost);
                        const refreshPostIndex = this.posts.findIndex(p => p.post_id === postId);
                        if (refreshPostIndex !== -1) {
                            this.posts[refreshPostIndex] = updatedPost;
                        }
                        this.updatePostInGrid(updatedPost);
                    }
                } catch (error) {
                    console.warn('Failed to refresh post data:', error);
                }
            }, 500);
        } else {
            // Extraction failed
            const errorMsg = completionData.progress?.error || 'Download failed';
            console.error('Download failed:', errorMsg);
            this.showStatus(`Failed to download files: ${errorMsg}`, 'error');

            try {
                const refreshResult = await API.metadata.getPost(postId);
                if (refreshResult.success) {
                    const updatedPost = refreshResult.data;
                    const refreshPostIndex = this.posts.findIndex(p => p.post_id === postId);
                    if (refreshPostIndex !== -1) {
                        this.posts[refreshPostIndex] = updatedPost;
                    }
                    this.updatePostInGrid(updatedPost);
                }
            } catch (error) {
                console.warn('Failed to refresh post after failed download:', error);
            }
        }
    }

    updateExtractionProgress(postId, progress, status = null) {
        const item = this.galleryGrid.querySelector(`[data-post-id="${postId}"]`);
        if (!item) return;
        
        console.log('Updating UI with progress:', progress);
        
        const progressCircle = item.querySelector('.progress-circle');
        const progressText = item.querySelector('.progress-text');
        const progressDetails = item.querySelector('.progress-details');
        
        if (progressCircle) {
            const degrees = (progress.percent / 100) * 360;
            progressCircle.style.setProperty('--progress', `${degrees}deg`);
        }
        
        if (progressText) {
            if (status === 'queued') {
                progressText.textContent = progress.message || 'Queued for download...';
            } else {
                progressText.textContent = progress.message || 'Processing...';
            }
        }
        
        if (progressDetails) {
            if (status === 'queued') {
                progressDetails.textContent = 'Queued';
            } else {
                progressDetails.textContent = `${progress.percent}%`;
            }
        }
    }

    async deleteExtracted(post) {
        const confirmed = confirm(
            'Remove downloaded files from this post?\n\nThis will remove all extracted content (HTML, JSON, media files). The original zip file will remain.'
        );
        
        if (!confirmed) return;
        
        try {
            this.setExtractionLoading(post, true, 'unextract');
            this.showStatus('Removing generated files...', 'info');
            
            const result = await API.delete(`/posts/${post.post_id}/files`);
            
            if (result.success) {
                // Update post data
                if (post.zip_files) {
                    post.zip_files.forEach(zip => zip.extracted = false);
                }
                this.updatePostInGrid(post);
                this.showStatus('Generated files removed successfully', 'success');
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('Failed to unextract:', error);
            this.showStatus(`Failed to remove files: ${error.message}`, 'error');
        } finally {
            this.setExtractionLoading(post, false);
        }
    }
    
    setExtractionLoading(post, loading, operation = 'extract') {
        const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
        if (!item) return;
        
        const extractionProgress = item.querySelector('.extraction-progress');
        const progressCircle = item.querySelector('.progress-circle');
        const progressText = item.querySelector('.progress-text');
        const progressDetails = item.querySelector('.progress-details');
        const extractBtn = item.querySelector('.edit-btn.extract');
        
        if (loading) {
            // Show progress overlay
            if (extractionProgress) {
                extractionProgress.classList.add('active');
            }
            
            // Set initial state
            if (progressCircle) {
                progressCircle.style.setProperty('--progress', '0deg');
            }
            if (progressText) {
                if (operation === 'queued') {
                    progressText.textContent = 'Queued for download...';
                } else {
                    progressText.textContent = operation === 'extract' ? 'Starting download...' : 'Removing files...';
                }
            }
            if (progressDetails) {
                if (operation === 'queued') {
                    progressDetails.textContent = 'Queued';
                } else {
                    progressDetails.textContent = operation === 'extract' ? '0%' : 'Please wait';
                }
            }
            
            // Disable extract button
            if (extractBtn) {
                extractBtn.disabled = true;
                extractBtn.style.opacity = '0.5';
            }
        } else {
            // Hide progress overlay
            if (extractionProgress) {
                extractionProgress.classList.remove('active');
            }
            
            // Re-enable extract button and update its state
            if (extractBtn) {
                extractBtn.disabled = false;
                extractBtn.style.opacity = '1';
                
                const hasExtracted = post.zip_files && post.zip_files.some(zip => zip.extracted);
                extractBtn.className = `edit-btn extract ${hasExtracted ? 'extracted' : ''}`;
                extractBtn.innerHTML = `<i class="fas fa-${hasExtracted ? 'folder-minus' : 'folder-plus'}"></i><span class="btn-text">${hasExtracted ? 'Remove Files' : 'Download Files'}</span>`;
            }
        }
    }

    startRename(post) {
        const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
        if (item) {
            const renameContainer = item.querySelector('.rename-container');
            const input = renameContainer.querySelector('.rename-input');
            
            renameContainer.classList.add('active');
            input.focus();
            input.select();
        }
    }

    async saveRename(post, newName) {
        if (!Utils.validate.postName(newName)) {
            statusManager.showError('Please enter a valid post name');
            return;
        }
        
        if (newName === post.revised_post_name) {
            this.cancelRename(post);
            return;
        }
        
        try {
            const result = await API.metadata.updatePost(post.post_id, {
                revised_post_name: newName
            });
            
            if (result.success) {
                post.revised_post_name = newName;
                this.updatePostInGrid(post);
                this.cancelRename(post);
                statusManager.showSuccess('Post renamed successfully');
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            statusManager.showError(`Failed to rename post: ${error.message}`);
        }
    }

    cancelRename(post) {
        const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
        if (item) {
            const renameContainer = item.querySelector('.rename-container');
            const input = renameContainer.querySelector('.rename-input');
            
            renameContainer.classList.remove('active');
            input.value = post.revised_post_name; // Reset to original value
        }
    }

    updatePostInGrid(post) {
        const item = this.galleryGrid.querySelector(`[data-post-id="${post.post_id}"]`);
        if (item) {
            // Update the item with new data
            const newItem = this.createGalleryItem(post);
            item.parentNode.replaceChild(newItem, item);
            
            // If edit mode is active, ensure the new item reflects that
            if (this.editMode) {
                newItem.classList.add('edit-mode');
            }
            
            // Also update the post in our posts array
            const postIndex = this.posts.findIndex(p => p.post_id === post.post_id);
            if (postIndex !== -1) {
                this.posts[postIndex] = { ...this.posts[postIndex], ...post };
            }
        }
    }
    
    showStatus(message, type = 'info') {
        // Use the global statusManager instead of custom notifications
        switch(type) {
            case 'success':
                statusManager.showSuccess(message);
                break;
            case 'error':
                statusManager.showError(message);
                break;
            case 'warning':
                statusManager.showWarning(message);
                break;
            default:
                statusManager.showInfo(message);
        }
    }

    async toggleFavourite(post, buttonElement) {
        try {
            // Toggle the favourite state
            const newFavouriteState = !post.favourite;
            
            // Optimistically update UI
            const icon = buttonElement.querySelector('i');
            if (newFavouriteState) {
                icon.className = 'fas fa-heart';
                buttonElement.classList.add('active');
                buttonElement.title = 'Remove from favourites';
            } else {
                icon.className = 'far fa-heart';
                buttonElement.classList.remove('active');
                buttonElement.title = 'Add to favourites';
            }
            
            // Update backend
            const result = await API.put(`/posts/${post.post_id}`, {
                favourite: newFavouriteState
            });
            
            if (result.success) {
                // Update local post data
                post.favourite = newFavouriteState;
                
                // Show brief feedback
                this.showStatus(
                    newFavouriteState ? 'Added to favourites ❤️' : 'Removed from favourites',
                    'success'
                );
            } else {
                throw new Error(result.error || 'Failed to update favourite status');
            }
        } catch (error) {
            console.error('Failed to toggle favourite:', error);
            this.showStatus(`Failed to update favourite: ${error.message}`, 'error');
            
            // Revert UI on error
            const icon = buttonElement.querySelector('i');
            if (post.favourite) {
                icon.className = 'fas fa-heart';
                buttonElement.classList.add('active');
                buttonElement.title = 'Remove from favourites';
            } else {
                icon.className = 'far fa-heart';
                buttonElement.classList.remove('active');
                buttonElement.title = 'Add to favourites';
            }
        }
    }

    async refreshPosts() {
        // Clear cache and reload
        API.cache.clearPattern('metadata/posts');
        await this.loadPosts();
    }
}

// Export for use in main.js
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Gallery;
}