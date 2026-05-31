# VAMA Gallery - Production Grade Web Application

A sophisticated web application for gallery management and file organization with aesthetic UI and comprehensive functionality.

## Features

### Gallery Pillar
- **Aesthetic Gallery View**: Clean, responsive grid layout with profile images
- **Advanced Search**: Real-time search across post names, descriptions, and IDs
- **Flexible Sorting**: Sort by date, name, ID with ascending/descending order
- **Smart Pagination**: Efficient pagination with customizable items per page
- **Profile Integration**: Each post linked to profile images with metadata display

### Image Viewing
- **Single Image View**: Focus on one image with smooth navigation (left/right arrows)
- **Cascade View**: View multiple images simultaneously with adjustable sizing
- **Smooth Transitions**: Seamless switching between view modes
- **Keyboard Navigation**: Full keyboard support for accessibility
- **Image Preloading**: Intelligent preloading for smooth experience

### Organization Pillar
- **Edit Mode**: Toggle-able edit interface for backend operations
- **Visibility Control**: Show/hide posts with persistent metadata updates
- **File Extraction**: Automated zip file extraction with status tracking
- **File Management**: Delete extracted files or complete post removal
- **Rename Functionality**: Live editing of post names with validation
- **Batch Operations**: Select multiple posts for bulk actions

### Cascade Edit Features
- **Drag & Drop Reordering**: Intuitive image reordering interface
- **Adjustable Image Size**: User-controllable image sizing with cookie persistence
- **Pagination in Cascade**: Handle hundreds of images with smooth pagination
- **Visual Feedback**: Clear indicators for drag operations and order changes

### Technical Excellence
- **Production-Grade Backend**: Flask API with comprehensive error handling
- **Real-time Updates**: WebSocket integration for live data synchronization
- **Responsive Design**: Works seamlessly on desktop, tablet, and mobile
- **Accessibility**: Full keyboard navigation and screen reader support
- **Performance Optimized**: Lazy loading, caching, and efficient data handling
- **Error Management**: Comprehensive error tracking and user feedback
- **State Persistence**: User preferences saved across sessions

## Architecture

### Frontend
- **HTML5**: Semantic markup with accessibility features
- **CSS3**: Modern styling with CSS Grid, Flexbox, and custom properties
- **Vanilla JavaScript**: Modular ES6+ architecture without framework dependencies
- **Progressive Enhancement**: Works without JavaScript for basic functionality

### Backend
- **Python Flask**: RESTful API with comprehensive endpoint coverage
- **JSON Metadata**: Efficient metadata management with caching
- **File Operations**: Secure file handling and extraction operations
- **Threading**: Background processing for time-intensive operations

### Key Components

#### Gallery.js
- Main gallery interface management
- Search, sort, and pagination logic
- Edit mode integration
- Real-time data synchronization

#### Viewer.js
- Single image and cascade view handling
- Image preloading and navigation
- Drag & drop reordering functionality
- Keyboard navigation support

#### API.js
- Centralized API communication layer
- Request retry logic and error handling
- Caching for improved performance
- WebSocket integration

#### Utils.js
- Utility functions for common operations
- Cookie and localStorage management
- DOM manipulation helpers
- Validation and formatting functions

## Setup Instructions

### Prerequisites
- Python 3.8+
- Modern web browser
- Access to the VAMA Project metadata structure

### Installation

1. **Install Python Dependencies**:
   ```bash
   cd "H:\VAMA Project\scripts\webapp"
   pip install -r requirements.txt
   ```

2. **Verify Directory Structure**:
   ```
   H:\VAMA Project\
   ├── scripts\
   │   ├── metadata\
   │   │   ├── posts_metadata.json
   │   │   └── profile_images\
   │   ├── downloads\
   │   └── webapp\
   │       ├── index.html
   │       ├── app.py
   │       ├── styles\
   │       └── js\
   ```

3. **Start the Backend**:
   ```bash
   python app.py
   ```

4. **Open Web Application**:
   Navigate to `http://127.0.0.1:5000/` in your web browser

### Configuration

The application automatically detects the VAMA Project structure and configures paths accordingly. Key configuration options in `app.py`:

- `METADATA_FILE`: Path to posts metadata JSON
- `PROFILE_IMAGES_DIR`: Directory containing profile images  
- `DOWNLOADS_DIR`: Directory with downloaded zip files
- `EXTRACTED_DIR`: Target directory for extracted content

## Usage Guide

### Basic Gallery Navigation
1. **Browse Posts**: Scroll through the gallery grid
2. **Search**: Use the search bar to find specific posts
3. **Sort**: Choose sorting criteria and order
4. **View Images**: Click any post to open the image viewer

### Edit Mode Operations
1. **Enable Edit Mode**: Click the "Edit Mode" button in the header
2. **Show/Hide Posts**: Toggle visibility without deleting
3. **Extract Files**: Process zip files to enable image viewing
4. **Rename Posts**: Click rename to edit post titles
5. **Delete Operations**: Remove extracted files or complete posts

### Image Viewing
1. **Single View**: Navigate images with arrow keys or buttons
2. **Cascade View**: See all images, adjust size with slider
3. **Reorder Images**: Enable edit mode in cascade view to drag & drop
4. **Keyboard Shortcuts**: Use arrow keys, space, escape for navigation

## API Endpoints

### Metadata Operations
- `GET /api/metadata/posts` - Get all posts with filtering
- `GET /api/metadata/posts/<post_id>` - Get specific post
- `PUT /api/metadata/posts/<post_id>` - Update post metadata

### File Operations
- `POST /api/files/extract/<post_id>` - Extract zip files
- `DELETE /api/files/extracted/<post_id>` - Delete extracted files
- `DELETE /api/files/all/<post_id>` - Delete all post files
- `GET /api/files/status/<post_id>` - Get extraction status

### Image Serving
- `GET /api/images/profile/<post_id>` - Get profile image
- `GET /api/images/content/<post_id>/<filename>` - Get content image
- `GET /api/images/thumbnail/<post_id>/<filename>` - Get thumbnail

### System Operations
- `GET /api/system/status` - Get system status
- `GET /api/system/disk-usage` - Get disk usage information
- `POST /api/system/clear-cache` - Clear system caches

## Advanced Features

### Keyboard Shortcuts
- `Ctrl+K`: Focus search bar
- `Ctrl+E`: Toggle edit mode
- `Ctrl+R`: Refresh data
- `Arrow Keys`: Navigate images/pages
- `Space`: Next image in viewer
- `Escape`: Close modals
- `C`: Switch to cascade view
- `S`: Switch to single view

### Responsive Design
The application adapts to different screen sizes:
- **Desktop**: Full feature set with optimal layout
- **Tablet**: Touch-friendly interface with maintained functionality
- **Mobile**: Simplified layout optimized for small screens

### Performance Optimizations
- **Lazy Loading**: Images load as needed
- **Caching**: API responses cached for improved speed
- **Background Processing**: File operations don't block UI
- **Debounced Search**: Efficient search without excessive API calls
- **Virtual Pagination**: Handle large datasets efficiently

## Future Extensibility

The application is architected for easy extension:
- **Modular Components**: Each feature in separate, reusable modules
- **Plugin Architecture**: Easy to add new functionality
- **API-First Design**: Backend can support multiple frontends
- **Flexible Metadata**: Structure supports additional fields
- **Theme System**: Ready for multiple UI themes
- **Internationalization Ready**: Structure supports multi-language

### Planned Features
- Favorites and playlists
- Tagging system
- Categories and collections
- Advanced search filters
- Thumbnail generation
- Image metadata extraction
- User preferences sync
- Export/import functionality

## Troubleshooting

### Common Issues

1. **Metadata not loading**: Check if `posts_metadata.json` exists and is valid JSON
2. **Images not displaying**: Verify profile images directory and file permissions
3. **Extraction failing**: Check if zip files exist in downloads directory
4. **Slow performance**: Clear cache via API or restart backend

### Logging
Check `vama_gallery.log` for detailed error information and system events.

### Browser Compatibility
- Chrome 80+
- Firefox 75+
- Safari 13+
- Edge 80+

## Contributing

The codebase follows modern JavaScript and Python conventions with:
- Comprehensive error handling
- Extensive logging
- Modular architecture
- Clean separation of concerns
- Production-ready patterns

For modifications, maintain the existing architectural patterns and ensure proper error handling throughout.