// Real Android ARM64 libvulkan probe. Runs only in an explicitly selected disposable guest.
#include <vulkan/vulkan.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "shader_spv.h"

#define CHECK(call) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) { \
    fprintf(stderr, "%s returned %d at line %d\n", #call, r_, __LINE__); exit(2); } } while (0)

static VkInstance instance;
static VkPhysicalDevice physical;
static VkDevice device;
static VkQueue queue;
static uint32_t queue_family;
static VkCommandPool command_pool;
static VkPhysicalDeviceMemoryProperties memory_properties;
static VkExtensionProperties *extensions;
static uint32_t extension_count;
static int has_template_khr, has_bind_khr;
static PFN_vkCreateDescriptorUpdateTemplate create_core, create_khr;
static PFN_vkUpdateDescriptorSetWithTemplate update_core, update_khr;
static PFN_vkDestroyDescriptorUpdateTemplate destroy_core, destroy_khr;
static PFN_vkBindBufferMemory2 bind_buffer_core, bind_buffer_khr;
static PFN_vkBindImageMemory2 bind_image_core, bind_image_khr;

static int has_extension(const char *name) {
    for (uint32_t i = 0; i < extension_count; ++i) {
        if (!strcmp(extensions[i].extensionName, name)) return 1;
    }
    return 0;
}

static uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags flags) {
    for (uint32_t i = 0; i < memory_properties.memoryTypeCount; ++i) {
        if ((bits & (1u << i)) && (memory_properties.memoryTypes[i].propertyFlags & flags) == flags) return i;
    }
    return UINT32_MAX;
}

typedef struct Buffer {
    VkBuffer handle;
    VkDeviceMemory memory;
    uint32_t *mapped;
} Buffer;

static Buffer make_buffer(VkBufferUsageFlags usage, uint32_t value) {
    Buffer b = {0};
    VkBufferCreateInfo ci = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = 256, .usage = usage, .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
    CHECK(vkCreateBuffer(device, &ci, NULL, &b.handle));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, b.handle, &req);
    uint32_t type = memory_type(req.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (type == UINT32_MAX) { fprintf(stderr, "no coherent host-visible buffer memory\n"); exit(2); }
    VkMemoryAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = req.size, .memoryTypeIndex = type };
    CHECK(vkAllocateMemory(device, &ai, NULL, &b.memory));
    CHECK(vkBindBufferMemory(device, b.handle, b.memory, 0));
    CHECK(vkMapMemory(device, b.memory, 0, VK_WHOLE_SIZE, 0, (void **)&b.mapped));
    for (unsigned i = 0; i < 64; ++i) b.mapped[i] = value;
    return b;
}

static void free_buffer(Buffer *b) {
    vkUnmapMemory(device, b->memory);
    vkDestroyBuffer(device, b->handle, NULL);
    vkFreeMemory(device, b->memory, NULL);
}

static VkCommandBuffer begin_commands(void) {
    VkCommandBuffer cmd;
    VkCommandBufferAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = command_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
    CHECK(vkAllocateCommandBuffers(device, &ai, &cmd));
    VkCommandBufferBeginInfo bi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
    CHECK(vkBeginCommandBuffer(cmd, &bi));
    return cmd;
}

static void finish_commands(VkCommandBuffer cmd) {
    CHECK(vkEndCommandBuffer(cmd));
    VkFence fence;
    VkFenceCreateInfo fi = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    CHECK(vkCreateFence(device, &fi, NULL, &fence));
    VkSubmitInfo submit = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1, .pCommandBuffers = &cmd };
    CHECK(vkQueueSubmit(queue, 1, &submit, fence));
    CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, 15000000000ULL));
    vkDestroyFence(device, fence, NULL);
    vkFreeCommandBuffers(device, command_pool, 1, &cmd);
}

typedef struct Image { VkImage handle; VkDeviceMemory memory; VkImageView view; } Image;

static Image make_image(uint32_t value) {
    Image image = {0};
    VkImageCreateInfo ci = { .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R32G32B32A32_UINT,
        .extent = {1,1,1}, .mipLevels = 1, .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED };
    CHECK(vkCreateImage(device, &ci, NULL, &image.handle));
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(device, image.handle, &req);
    uint32_t type = memory_type(req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (type == UINT32_MAX) type = memory_type(req.memoryTypeBits, 0);
    VkMemoryAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = req.size, .memoryTypeIndex = type };
    CHECK(vkAllocateMemory(device, &ai, NULL, &image.memory));
    CHECK(vkBindImageMemory(device, image.handle, image.memory, 0));
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT,0,1,0,1};
    VkImageViewCreateInfo vi = { .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image = image.handle, .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format = ci.format, .subresourceRange = range };
    CHECK(vkCreateImageView(device, &vi, NULL, &image.view));
    VkCommandBuffer cmd = begin_commands();
    VkImageMemoryBarrier barrier = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image.handle, .subresourceRange = range };
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
        0, 0, NULL, 0, NULL, 1, &barrier);
    VkClearColorValue clear = { .uint32 = {value,value,value,value} };
    vkCmdClearColorImage(cmd, image.handle, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &clear, 1, &range);
    barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
        0, 0, NULL, 0, NULL, 1, &barrier);
    finish_commands(cmd);
    return image;
}

static void free_image(Image *image) {
    vkDestroyImageView(device, image->view, NULL);
    vkDestroyImage(device, image->handle, NULL);
    vkFreeMemory(device, image->memory, NULL);
}

static VkPipeline make_pipeline(VkPipelineLayout layout, const uint32_t *code, size_t size) {
    VkShaderModule module;
    VkShaderModuleCreateInfo mi = { .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = size, .pCode = code };
    CHECK(vkCreateShaderModule(device, &mi, NULL, &module));
    VkComputePipelineCreateInfo pi = { .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = module, .pName = "main" },
        .layout = layout };
    VkPipeline pipeline;
    CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pi, NULL, &pipeline));
    vkDestroyShaderModule(device, module, NULL);
    return pipeline;
}

static void dispatch(VkPipeline pipeline, VkPipelineLayout layout, VkDescriptorSet set) {
    VkCommandBuffer cmd = begin_commands();
    VkMemoryBarrier before = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_HOST_WRITE_BIT, .dstAccessMask = VK_ACCESS_SHADER_READ_BIT };
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
        0, 1, &before, 0, NULL, 0, NULL);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, NULL);
    vkCmdDispatch(cmd, 1, 1, 1);
    VkMemoryBarrier after = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT };
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
        0, 1, &after, 0, NULL, 0, NULL);
    finish_commands(cmd);
}

// Each template has an array entry followed by an entry of the same descriptor type.
// Nontrivial offsets/strides exercise linearization. Ordinary writes first bind all
// inputs to a valid sentinel, so a dropped raw KHR update gives wrong numbers safely.
static int descriptor_case(unsigned kind) {
    static const char *names[] = {"buffer", "image", "view"};
    static const uint32_t values[3][3] = {{7,11,17},{19,23,29},{31,37,41}};
    const uint32_t *expected = values[kind];
    VkDescriptorType type = kind == 0 ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER :
        kind == 1 ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_UNIFORM_TEXEL_BUFFER;
    Buffer inputs[3] = {0};
    Image images[3] = {0};
    VkBufferView views[3] = {0};
    VkDescriptorBufferInfo buffer_info[3] = {0};
    VkDescriptorImageInfo image_info[3] = {0};
    for (unsigned i = 0; i < 3; ++i) {
        if (kind == 1) {
            images[i] = make_image(expected[i]);
            image_info[i] = (VkDescriptorImageInfo){VK_NULL_HANDLE,images[i].view,VK_IMAGE_LAYOUT_GENERAL};
        } else {
            inputs[i] = make_buffer(kind == 0 ? VK_BUFFER_USAGE_STORAGE_BUFFER_BIT : VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT, expected[i]);
            buffer_info[i] = (VkDescriptorBufferInfo){inputs[i].handle,0,256};
            if (kind == 2) {
                VkBufferViewCreateInfo vi = { .sType = VK_STRUCTURE_TYPE_BUFFER_VIEW_CREATE_INFO,
                    .buffer = inputs[i].handle, .format = VK_FORMAT_R32_UINT, .range = 256 };
                CHECK(vkCreateBufferView(device, &vi, NULL, &views[i]));
            }
        }
    }
    Buffer output = make_buffer(VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, 0xdeadbeef);
    VkDescriptorBufferInfo output_info = {output.handle,0,256};
    VkDescriptorSetLayoutBinding bindings[3] = {
        {0,type,2,VK_SHADER_STAGE_COMPUTE_BIT,NULL},
        {1,type,1,VK_SHADER_STAGE_COMPUTE_BIT,NULL},
        {2,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1,VK_SHADER_STAGE_COMPUTE_BIT,NULL} };
    VkDescriptorSetLayoutCreateInfo li = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 3, .pBindings = bindings };
    VkDescriptorSetLayout set_layout;
    CHECK(vkCreateDescriptorSetLayout(device, &li, NULL, &set_layout));
    VkPipelineLayoutCreateInfo pli = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &set_layout };
    VkPipelineLayout pipeline_layout;
    CHECK(vkCreatePipelineLayout(device, &pli, NULL, &pipeline_layout));
    const uint32_t *code = kind == 0 ? buffer_spv : kind == 1 ? image_spv : view_spv;
    size_t code_size = kind == 0 ? sizeof(buffer_spv) : kind == 1 ? sizeof(image_spv) : sizeof(view_spv);
    VkPipeline pipeline = make_pipeline(pipeline_layout, code, code_size);
    VkDescriptorPoolSize sizes[2] = {{type,3},{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,1}};
    unsigned size_count = 2;
    if (kind == 0) { sizes[0].descriptorCount = 4; size_count = 1; }
    VkDescriptorPoolCreateInfo pci = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1, .poolSizeCount = size_count, .pPoolSizes = sizes };
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(device, &pci, NULL, &pool));
    VkDescriptorSetAllocateInfo sai = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = pool, .descriptorSetCount = 1, .pSetLayouts = &set_layout };
    VkDescriptorSet set;
    CHECK(vkAllocateDescriptorSets(device, &sai, &set));
    struct BufferPayload { uint64_t pad; VkDescriptorBufferInfo array[2]; uint64_t gap[3]; VkDescriptorBufferInfo tail; } bp = {0};
    struct ImagePayload { uint64_t pad; VkDescriptorImageInfo array[2]; uint64_t gap[3]; VkDescriptorImageInfo tail; } ip = {0};
    struct ViewPayload { uint64_t pad; VkBufferView array[2]; uint64_t gap[3]; VkBufferView tail; } vp = {0};
    void *payload;
    size_t first_offset, tail_offset, stride;
    if (kind == 0) {
        bp.array[0] = buffer_info[0]; bp.array[1] = buffer_info[1]; bp.tail = buffer_info[2];
        payload = &bp; first_offset = offsetof(struct BufferPayload,array); tail_offset = offsetof(struct BufferPayload,tail); stride = sizeof(VkDescriptorBufferInfo);
    } else if (kind == 1) {
        ip.array[0] = image_info[0]; ip.array[1] = image_info[1]; ip.tail = image_info[2];
        payload = &ip; first_offset = offsetof(struct ImagePayload,array); tail_offset = offsetof(struct ImagePayload,tail); stride = sizeof(VkDescriptorImageInfo);
    } else {
        vp.array[0] = views[0]; vp.array[1] = views[1]; vp.tail = views[2];
        payload = &vp; first_offset = offsetof(struct ViewPayload,array); tail_offset = offsetof(struct ViewPayload,tail); stride = sizeof(VkBufferView);
    }
    VkDescriptorUpdateTemplateEntry entries[2] = {
        {0,0,2,type,first_offset,stride}, {1,0,1,type,tail_offset,stride} };
    VkDescriptorUpdateTemplateCreateInfo ti = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_UPDATE_TEMPLATE_CREATE_INFO,
        .descriptorUpdateEntryCount = 2, .pDescriptorUpdateEntries = entries,
        .templateType = VK_DESCRIPTOR_UPDATE_TEMPLATE_TYPE_DESCRIPTOR_SET,
        .descriptorSetLayout = set_layout, .pipelineBindPoint = VK_PIPELINE_BIND_POINT_COMPUTE };
    // Establish a distinct-value reference with ordinary descriptor writes before
    // testing templates. This separates shader/readback failures from marshaling.
    VkWriteDescriptorSet reference[3] = {
        { .sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=0,.descriptorCount=2,.descriptorType=type,
          .pBufferInfo=kind==0?buffer_info:NULL,.pImageInfo=kind==1?image_info:NULL,.pTexelBufferView=kind==2?views:NULL },
        { .sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=1,.descriptorCount=1,.descriptorType=type,
          .pBufferInfo=kind==0?&buffer_info[2]:NULL,.pImageInfo=kind==1?&image_info[2]:NULL,.pTexelBufferView=kind==2?&views[2]:NULL },
        { .sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=2,.descriptorCount=1,
          .descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&output_info } };
    vkUpdateDescriptorSets(device,3,reference,0,NULL);
    dispatch(pipeline,pipeline_layout,set);
    int okay = output.mapped[0]==expected[0] && output.mapped[1]==expected[1] && output.mapped[2]==expected[2];
    printf("%s{\"type\":\"%s\",\"expected\":[%u,%u,%u],\"reference\":{\"observed\":[%u,%u,%u],\"pass\":%s},\"updates\":[",
        kind ? "," : "", names[kind],expected[0],expected[1],expected[2],output.mapped[0],output.mapped[1],output.mapped[2],okay?"true":"false");
    for (unsigned mode = 0; mode < 4; ++mode) {
        int create_alias = (mode >> 1) & 1, update_alias = mode & 1;
        PFN_vkCreateDescriptorUpdateTemplate create = create_alias ? create_khr : create_core;
        PFN_vkUpdateDescriptorSetWithTemplate update = update_alias ? update_khr : update_core;
        PFN_vkDestroyDescriptorUpdateTemplate destroy = create_alias ? destroy_khr : destroy_core;
        if ((create_alias || update_alias) && !has_template_khr) { create = NULL; update = NULL; }
        printf("%s{\"create\":\"%s\",\"update\":\"%s\",",mode ? "," : "",create_alias?"KHR":"core",update_alias?"KHR":"core");
        if (!create || !update || !destroy) { printf("\"available\":false,\"pass\":false}");okay=0;continue; }
        VkDescriptorBufferInfo safe_buffers[2] = {buffer_info[0],buffer_info[0]};
        VkDescriptorImageInfo safe_images[2] = {image_info[0],image_info[0]};
        VkBufferView safe_views[2] = {views[0],views[0]};
        VkWriteDescriptorSet writes[3] = {
            { .sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=0,.descriptorCount=2,.descriptorType=type,
              .pBufferInfo=kind==0?safe_buffers:NULL,.pImageInfo=kind==1?safe_images:NULL,.pTexelBufferView=kind==2?safe_views:NULL },
            { .sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=1,.descriptorCount=1,.descriptorType=type,
              .pBufferInfo=kind==0?&buffer_info[0]:NULL,.pImageInfo=kind==1?&image_info[0]:NULL,.pTexelBufferView=kind==2?&views[0]:NULL },
            { .sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=set,.dstBinding=2,.descriptorCount=1,
              .descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&output_info } };
        vkUpdateDescriptorSets(device,3,writes,0,NULL);
        output.mapped[0]=output.mapped[1]=output.mapped[2]=0xdeadbeef;
        VkDescriptorUpdateTemplate templ;
        CHECK(create(device,&ti,NULL,&templ));
        update(device,set,templ,payload);
        dispatch(pipeline,pipeline_layout,set);
        int pass=output.mapped[0]==expected[0] && output.mapped[1]==expected[1] && output.mapped[2]==expected[2];
        printf("\"available\":true,\"observed\":[%u,%u,%u],\"pass\":%s}",output.mapped[0],output.mapped[1],output.mapped[2],pass?"true":"false");
        fflush(stdout);okay &= pass;
        destroy(device,templ,NULL);
    }
    printf("]}");
    vkDestroyDescriptorPool(device,pool,NULL);
    vkDestroyPipeline(device,pipeline,NULL);
    vkDestroyPipelineLayout(device,pipeline_layout,NULL);
    vkDestroyDescriptorSetLayout(device,set_layout,NULL);
    free_buffer(&output);
    for (unsigned i=0;i<3;++i) {
        if (kind==1) free_image(&images[i]);
        else { if (views[i]) vkDestroyBufferView(device,views[i],NULL);free_buffer(&inputs[i]); }
    }
    return okay;
}

typedef struct Stress {
    unsigned iterations;
    atomic_uint completed;
    atomic_int failure;
    atomic_uint calls[3];
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    unsigned arrived;
    int start;
} Stress;

static int stress_result(Stress *stress,VkResult result) {
    if (result==VK_SUCCESS) return 1;
    int expected=0;atomic_compare_exchange_strong(&stress->failure,&expected,(int)result);return 0;
}

static void *bind_worker(void *opaque) {
    Stress *stress=opaque;
    pthread_mutex_lock(&stress->mutex);
    ++stress->arrived;pthread_cond_broadcast(&stress->condition);
    while (!stress->start) pthread_cond_wait(&stress->condition,&stress->mutex);
    pthread_mutex_unlock(&stress->mutex);
    for (unsigned i=0;i<stress->iterations && !atomic_load(&stress->failure);++i) {
        VkImage image=VK_NULL_HANDLE;VkBuffer buffer=VK_NULL_HANDLE;
        VkDeviceMemory image_memory=VK_NULL_HANDLE,buffer_memory=VK_NULL_HANDLE;
        VkImageCreateInfo ici={.sType=VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,.imageType=VK_IMAGE_TYPE_2D,
            .format=VK_FORMAT_R8G8B8A8_UNORM,.extent={64,64,1},.mipLevels=1,.arrayLayers=1,
            .samples=VK_SAMPLE_COUNT_1_BIT,.tiling=VK_IMAGE_TILING_OPTIMAL,
            .usage=VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT,
            .sharingMode=VK_SHARING_MODE_EXCLUSIVE,.initialLayout=VK_IMAGE_LAYOUT_UNDEFINED};
        VkBufferCreateInfo bci={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=4096,
            .usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT|VK_BUFFER_USAGE_TRANSFER_DST_BIT,.sharingMode=VK_SHARING_MODE_EXCLUSIVE};
        if (!stress_result(stress,vkCreateImage(device,&ici,NULL,&image))) goto cleanup;
        if (!stress_result(stress,vkCreateBuffer(device,&bci,NULL,&buffer))) goto cleanup;
        VkMemoryRequirements ir,br;
        vkGetImageMemoryRequirements(device,image,&ir);vkGetBufferMemoryRequirements(device,buffer,&br);
        uint32_t it=memory_type(ir.memoryTypeBits,0),bt=memory_type(br.memoryTypeBits,0);
        if (it==UINT32_MAX||bt==UINT32_MAX) {stress_result(stress,VK_ERROR_FEATURE_NOT_PRESENT);goto cleanup;}
        VkMemoryAllocateInfo ai={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=ir.size,.memoryTypeIndex=it};
        if (!stress_result(stress,vkAllocateMemory(device,&ai,NULL,&image_memory))) goto cleanup;
        ai.allocationSize=br.size;ai.memoryTypeIndex=bt;
        if (!stress_result(stress,vkAllocateMemory(device,&ai,NULL,&buffer_memory))) goto cleanup;
        unsigned mode=i%3;
        if (mode==2 && (!has_bind_khr||!bind_image_khr||!bind_buffer_khr)) mode=0;
        if (mode==1 && (!bind_image_core||!bind_buffer_core)) mode=0;
        VkResult image_result,buffer_result;
        if (mode==0) {image_result=vkBindImageMemory(device,image,image_memory,0);buffer_result=vkBindBufferMemory(device,buffer,buffer_memory,0);}
        else {
            VkBindImageMemoryInfo ii={.sType=VK_STRUCTURE_TYPE_BIND_IMAGE_MEMORY_INFO,.image=image,.memory=image_memory,.memoryOffset=0};
            VkBindBufferMemoryInfo bi={.sType=VK_STRUCTURE_TYPE_BIND_BUFFER_MEMORY_INFO,.buffer=buffer,.memory=buffer_memory,.memoryOffset=0};
            image_result=(mode==1?bind_image_core:bind_image_khr)(device,1,&ii);
            buffer_result=(mode==1?bind_buffer_core:bind_buffer_khr)(device,1,&bi);
        }
        if (!stress_result(stress,image_result)||!stress_result(stress,buffer_result)) goto cleanup;
        atomic_fetch_add(&stress->calls[mode],2);atomic_fetch_add(&stress->completed,1);
    cleanup:
        if (image) vkDestroyImage(device,image,NULL);
        if (buffer) vkDestroyBuffer(device,buffer,NULL);
        if (image_memory) vkFreeMemory(device,image_memory,NULL);
        if (buffer_memory) vkFreeMemory(device,buffer_memory,NULL);
    }
    return NULL;
}

static int bind_stress(unsigned iterations) {
    Stress stress={.iterations=iterations,.mutex=PTHREAD_MUTEX_INITIALIZER,.condition=PTHREAD_COND_INITIALIZER};
    pthread_t threads[4];
    for (unsigned i=0;i<4;++i) {int e=pthread_create(&threads[i],NULL,bind_worker,&stress);if(e){fprintf(stderr,"pthread_create: %d\n",e);exit(2);}}
    pthread_mutex_lock(&stress.mutex);
    while (stress.arrived<4) pthread_cond_wait(&stress.condition,&stress.mutex);
    stress.start=1;pthread_cond_broadcast(&stress.condition);pthread_mutex_unlock(&stress.mutex);
    for (unsigned i=0;i<4;++i) pthread_join(threads[i],NULL);
    int pass=!atomic_load(&stress.failure) && atomic_load(&stress.completed)==4*iterations;
    printf("\"bind_stress\":{\"threads\":4,\"iterations_per_thread\":%u,\"completed_pairs\":%u,\"bind_calls\":{\"original\":%u,\"core2\":%u,\"KHR2\":%u},\"vk_error\":%d,\"pass\":%s}",
        iterations,atomic_load(&stress.completed),atomic_load(&stress.calls[0]),atomic_load(&stress.calls[1]),atomic_load(&stress.calls[2]),atomic_load(&stress.failure),pass?"true":"false");
    pthread_mutex_destroy(&stress.mutex);pthread_cond_destroy(&stress.condition);
    return pass;
}

static int pnext_query(void) {
    VkPhysicalDeviceDescriptorBufferPropertiesEXT descriptor;
    VkPhysicalDeviceAccelerationStructurePropertiesKHR acceleration;
    VkPhysicalDeviceRayTracingPipelinePropertiesKHR ray;
    memset(&descriptor,0xa5,sizeof(descriptor));memset(&acceleration,0xa5,sizeof(acceleration));memset(&ray,0xa5,sizeof(ray));
    descriptor.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_BUFFER_PROPERTIES_EXT;descriptor.pNext=&acceleration;
    acceleration.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_PROPERTIES_KHR;acceleration.pNext=&ray;
    ray.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_TRACING_PIPELINE_PROPERTIES_KHR;ray.pNext=NULL;
    VkPhysicalDeviceProperties2 properties={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,.pNext=&descriptor};
    vkGetPhysicalDeviceProperties2(physical,&properties);
    int links=properties.pNext==&descriptor && descriptor.pNext==&acceleration && acceleration.pNext==&ray && ray.pNext==NULL;
    int descriptor_absent=!has_extension(VK_EXT_DESCRIPTOR_BUFFER_EXTENSION_NAME);
    int acceleration_absent=!has_extension(VK_KHR_ACCELERATION_STRUCTURE_EXTENSION_NAME);
    int ray_absent=!has_extension(VK_KHR_RAY_TRACING_PIPELINE_EXTENSION_NAME);
    int cleared=(!descriptor_absent || (!descriptor.samplerDescriptorSize && !descriptor.uniformBufferDescriptorSize && !descriptor.descriptorBufferOffsetAlignment)) &&
        (!acceleration_absent || (!acceleration.maxGeometryCount && !acceleration.maxInstanceCount && !acceleration.maxPrimitiveCount)) &&
        (!ray_absent || (!ray.shaderGroupHandleSize && !ray.maxRayRecursionDepth && !ray.shaderGroupHandleCaptureReplaySize));
    int pass=links&&cleared;
    printf("\"pnext_query\":{\"sizeof_size_t\":%zu,\"links_preserved\":%s,\"descriptor_extension_absent\":%s,\"sampler_descriptor_size\":%zu,\"acceleration_extension_absent\":%s,\"max_geometry_count\":%llu,\"ray_extension_absent\":%s,\"shader_group_handle_size\":%u,\"pass\":%s}",
        sizeof(size_t),links?"true":"false",descriptor_absent?"true":"false",descriptor.samplerDescriptorSize,acceleration_absent?"true":"false",(unsigned long long)acceleration.maxGeometryCount,ray_absent?"true":"false",ray.shaderGroupHandleSize,pass?"true":"false");
    return pass;
}

int main(int argc,char **argv) {
    unsigned iterations=128;
    if (argc==2) {char *end;unsigned long n=strtoul(argv[1],&end,10);if(*end||n<1||n>4096){fprintf(stderr,"iterations must be 1..4096\n");return 2;}iterations=(unsigned)n;}
    if (argc>2) return 2;
    VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="ModAVD ARM Vulkan probe",.apiVersion=VK_API_VERSION_1_1};
    VkInstanceCreateInfo ici={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};
    CHECK(vkCreateInstance(&ici,NULL,&instance));
    uint32_t count=0;CHECK(vkEnumeratePhysicalDevices(instance,&count,NULL));if(!count){fprintf(stderr,"no Vulkan devices\n");return 2;}
    VkPhysicalDevice *devices=calloc(count,sizeof(*devices));CHECK(vkEnumeratePhysicalDevices(instance,&count,devices));physical=devices[0];free(devices);
    VkPhysicalDeviceProperties props;vkGetPhysicalDeviceProperties(physical,&props);
    if(props.apiVersion<VK_API_VERSION_1_1){fprintf(stderr,"Vulkan 1.1 required\n");return 2;}
    vkGetPhysicalDeviceMemoryProperties(physical,&memory_properties);
    CHECK(vkEnumerateDeviceExtensionProperties(physical,NULL,&extension_count,NULL));extensions=calloc(extension_count,sizeof(*extensions));CHECK(vkEnumerateDeviceExtensionProperties(physical,NULL,&extension_count,extensions));
    has_template_khr=has_extension(VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME);has_bind_khr=has_extension(VK_KHR_BIND_MEMORY_2_EXTENSION_NAME);
    const char *enabled[2];uint32_t enabled_count=0;if(has_template_khr)enabled[enabled_count++]=VK_KHR_DESCRIPTOR_UPDATE_TEMPLATE_EXTENSION_NAME;if(has_bind_khr)enabled[enabled_count++]=VK_KHR_BIND_MEMORY_2_EXTENSION_NAME;
    uint32_t qc=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&qc,NULL);VkQueueFamilyProperties *qp=calloc(qc,sizeof(*qp));vkGetPhysicalDeviceQueueFamilyProperties(physical,&qc,qp);queue_family=UINT32_MAX;
    for(uint32_t i=0;i<qc;++i)if(qp[i].queueFlags&VK_QUEUE_COMPUTE_BIT){queue_family=i;break;}free(qp);if(queue_family==UINT32_MAX)return 2;
    float priority=1.0f;VkDeviceQueueCreateInfo qi={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=queue_family,.queueCount=1,.pQueuePriorities=&priority};
    VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qi,.enabledExtensionCount=enabled_count,.ppEnabledExtensionNames=enabled};
    CHECK(vkCreateDevice(physical,&dci,NULL,&device));vkGetDeviceQueue(device,queue_family,0,&queue);
    VkCommandPoolCreateInfo pci={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.flags=VK_COMMAND_POOL_CREATE_TRANSIENT_BIT,.queueFamilyIndex=queue_family};CHECK(vkCreateCommandPool(device,&pci,NULL,&command_pool));
#define LOAD(variable,type,name) variable=(type)vkGetDeviceProcAddr(device,name)
    LOAD(create_core,PFN_vkCreateDescriptorUpdateTemplate,"vkCreateDescriptorUpdateTemplate");LOAD(create_khr,PFN_vkCreateDescriptorUpdateTemplate,"vkCreateDescriptorUpdateTemplateKHR");
    LOAD(update_core,PFN_vkUpdateDescriptorSetWithTemplate,"vkUpdateDescriptorSetWithTemplate");LOAD(update_khr,PFN_vkUpdateDescriptorSetWithTemplate,"vkUpdateDescriptorSetWithTemplateKHR");
    LOAD(destroy_core,PFN_vkDestroyDescriptorUpdateTemplate,"vkDestroyDescriptorUpdateTemplate");LOAD(destroy_khr,PFN_vkDestroyDescriptorUpdateTemplate,"vkDestroyDescriptorUpdateTemplateKHR");
    LOAD(bind_buffer_core,PFN_vkBindBufferMemory2,"vkBindBufferMemory2");LOAD(bind_buffer_khr,PFN_vkBindBufferMemory2,"vkBindBufferMemory2KHR");
    LOAD(bind_image_core,PFN_vkBindImageMemory2,"vkBindImageMemory2");LOAD(bind_image_khr,PFN_vkBindImageMemory2,"vkBindImageMemory2KHR");
    printf("{\"schema\":1,\"abi\":\"arm64-v8a\",\"device\":\"%s\",\"api_version\":%u,\"KHR_template_enabled\":%s,\"KHR_bind_enabled\":%s,\"descriptors\":[",props.deviceName,props.apiVersion,has_template_khr?"true":"false",has_bind_khr?"true":"false");fflush(stdout);
    int pass=1;for(unsigned kind=0;kind<3;++kind)pass&=descriptor_case(kind);printf("],");
    pass&=pnext_query();printf(",");pass&=bind_stress(iterations);
    CHECK(vkDeviceWaitIdle(device));printf(",\"pass\":%s}\n",pass?"true":"false");
    vkDestroyCommandPool(device,command_pool,NULL);vkDestroyDevice(device,NULL);vkDestroyInstance(instance,NULL);free(extensions);
    return pass?0:1;
}
