import numpy, random, pdb, math, pickle, glob, time, os
import torch
from torch.autograd import Variable

class DataLoader():
    def __init__(self, fname, opt, dataset='simulator', single_shard=False):
        if opt.debug == 1:
            single_shard = True
        self.opt = opt
        self.data = []
        self.dataset = dataset
        self.random = random.Random()
        self.random.seed(12345) # use this so that the same batches will always be picked

        if dataset == 'i80':
            data_dir = '/misc/vlgscratch4/LecunGroup/nvidia-collab/data/data_i80_v2/'
            if single_shard:
                # quick load for debugging
                data_files = ['trajectories-0500-0515.txt/']
            else:
                data_files = ['trajectories-0400-0415/', 
                              'trajectories-0500-0515/', 
                              'trajectories-0515-0530/']

            self.images = []
            self.actions = []
            self.costs = []
            self.states = []
            for df in data_files:
                combined_data_path = data_dir + f'{df}/all_data.pth'
                if os.path.isfile(combined_data_path):
                    print(f'[loading data {combined_data_path}]')
                    data = torch.load(combined_data_path)
                    self.images += data.get('images')
                    self.actions += data.get('actions')
                    self.costs += data.get('costs')
                    self.states += data.get('states')
                else:
                    print(data_dir)
                    for f in glob.glob(data_dir + f'{df}/car*.pkl'):
                        print(f'[loading {f}]')
                        fd = pickle.load(open(f, 'rb'))
                        try:
                            T = fd.get('actions').size(0)
                            self.data += [{'images': fd.get('images'), 
                                           'actions': fd.get('actions'), 
                                           'costs': torch.cat((fd.get('proximity_cost').view(-1,1), fd.get('lane_cost')[:T].view(-1,1)), 1), 
                                           'states': fd.get('states'),
                                           'proximity_cost': fd.get('proximity_cost')}]
                        except:
                            pdb.set_trace()
                        
                    images = []
                    actions = []
                    costs = []
                    states = []
                    for run in self.data:
                        images.append(run.get('images'))
                        actions.append(run.get('actions'))
                        costs.append(run.get('costs'))
                        states.append(run.get('states'))
                    torch.save({'images': images, 'actions': actions, 'costs': costs, 'states': states}, combined_data_path)
                    self.images += images
                    self.actions += actions
                    self.costs += costs
                    self.states += states


            self.n_episodes = len(self.images)
            self.n_train = int(math.floor(self.n_episodes * 0.9))
            self.n_valid = int(math.floor(self.n_episodes * 0.05))
            self.n_test = int(math.floor(self.n_episodes * 0.05))
            splits_path = data_dir + '/splits.pth'
            if os.path.exists(splits_path):
                print('[loading data splits]')
                self.splits = torch.load(splits_path)                
                self.train_indx = self.splits.get('train_indx')
                self.valid_indx = self.splits.get('valid_indx')
                self.test_indx = self.splits.get('test_indx')
            else:
                print('[generating data splits]')
                perm = numpy.random.permutation(self.n_episodes)
                self.train_indx = perm[0:self.n_train]
                self.valid_indx = perm[self.n_train+1:self.n_train+self.n_valid]
                self.test_indx = perm[self.n_train+self.n_valid+1:self.n_train+self.n_valid+self.n_test]
                torch.save({'train_indx': self.train_indx, 'valid_indx': self.valid_indx, 'test_indx': self.test_indx}, splits_path)

        elif dataset == 'simulator':        
            for i in range(opt.nshards):
                f = f'{opt.data_dir}/traffic_data_lanes={opt.lanes}-episodes={opt.n_episodes}-seed={i+1}.pkl'
                print(f'[loading {f}]')
                self.data += pickle.load(open(f, 'rb'))

            self.images = []
            self.states = []
            self.actions = []
            self.masks = []
            for run in self.data:
                self.images.append(run.get('images'))
                self.actions.append(run.get('actions'))
                self.states.append(run.get('states'))
                self.masks.append(run.get('masks'))

        
            self.n_episodes = len(self.images)
            print(f'Number of episodes: {self.n_episodes}')
            self.n_train = int(math.floor(self.n_episodes * 0.9))
            self.n_valid = int(math.floor(self.n_episodes * 0.05))
            self.n_test = int(math.floor(self.n_episodes * 0.05))
            self.train_indx = range(0, self.n_train)
            self.valid_indx = range(self.n_train+1, self.n_train+self.n_valid)
            self.test_indx = range(self.n_train+self.n_valid+1, self.n_episodes)        
            self.test_indx_iterator = 0

        stats_path = data_dir + '/data_stats.pth'
        if os.path.isfile(stats_path):
            print(f'[loading data stats: {stats_path}]')
            stats = torch.load(stats_path)
            self.a_mean = stats.get('a_mean')
            self.a_std = stats.get('a_std')
            self.s_mean = stats.get('s_mean')
            self.s_std = stats.get('s_std')
        else:
            print('[computing action stats]')
            all_actions = []
            for i in self.train_indx:
                all_actions.append(self.actions[i])
            all_actions = torch.cat(all_actions, 0)
            self.a_mean = torch.mean(all_actions, 0)
            self.a_std = torch.std(all_actions, 0)
            print('[computing state stats]')
            all_states = []
            for i in self.train_indx:
                all_states.append(self.states[i][:, 0])
            all_states = torch.cat(all_states, 0)
            self.s_mean = torch.mean(all_states, 0)
            self.s_std = torch.std(all_states, 0)
            torch.save({'a_mean': self.a_mean, 
                        'a_std': self.a_std, 
                        's_mean': self.s_mean, 
                        's_std': self.s_std}, stats_path)




    # get batch to use for forward modeling
    # a sequence of ncond given states, a sequence of npred actions, 
    # and a sequence of npred states to be predicted
    def get_batch_fm(self, split, npred=-1, cuda=True):
        if self.opt.debug == 1 and False:
            self.opt.height = 117
            self.opt.width = 24
            self.opt.n_actions = 2
            input_images = torch.randn(self.opt.batch_size, self.opt.ncond, 3, self.opt.height, self.opt.width)
            actions = torch.randn(self.opt.batch_size, self.opt.npred, self.opt.n_actions)
            target_images = torch.randn(self.opt.batch_size, self.opt.npred, 3, self.opt.height, self.opt.width)
            return input_images.cuda(), actions.cuda(), target_images.cuda(), None, None 

        if split == 'train':
            indx = self.train_indx
        elif split == 'valid':
            indx = self.valid_indx
        elif split == 'test':
            indx = self.test_indx
            self.current_indx = 0
            t = 0

        if npred == -1:
            npred = self.opt.npred

        images, states, actions, costs = [], [], [], []
        nb = 0
        while nb < self.opt.batch_size:
            if self.opt.debug == 1:
                s = self.random.choice(range(0, len(self.images)))
            else:
                s = self.random.choice(indx)
            T = self.states[s].size(0)
            if T > (self.opt.ncond + npred + 1):
                t = self.random.randint(0, T - (self.opt.ncond+npred + 1))
                images.append(self.images[s][t:t+(self.opt.ncond+npred)+1].cuda())
                actions.append(self.actions[s][t:t+(self.opt.ncond+npred)].cuda())
                states.append(self.states[s][t:t+(self.opt.ncond+npred)+1].cuda())
                costs.append(self.costs[s][t:t+(self.opt.ncond+npred)+1].cuda())
                nb += 1
                            
        images = torch.stack(images).float()
        images.div_(255.0)

        try:
            states = torch.stack(states)
        except:
            pdb.set_trace()

#        states = states.contiguous()
        states = states[:, :, 0].contiguous()

        actions = torch.stack(actions)

        if self.opt.debug == 0:
            actions -= self.a_mean.view(1, 1, 2).expand(actions.size()).cuda()
            actions /= (1e-8 + self.a_std.view(1, 1, 2).expand(actions.size())).cuda()
            states -= self.s_mean.view(1, 1, 4).expand(states.size()).cuda()
            states /= (1e-8 + self.s_std.view(1, 1, 4).expand(states.size())).cuda()

        costs = torch.stack(costs)
        '''
        costs -= self.c_mean.view(1, 1, 2).expand(costs.size()).cuda()
        costs /= (1e-8 + self.c_std.view(1, 1, 2).expand(costs.size())).cuda()
        '''

        actions = actions[:, (self.opt.ncond-1):(self.opt.ncond+npred-1)].float().contiguous()
        input_images = images[:, :self.opt.ncond].float().contiguous()
        input_states = states[:, :self.opt.ncond].float().contiguous()
        target_images = images[:, self.opt.ncond:(self.opt.ncond+npred)].float().contiguous()
        target_states = states[:, self.opt.ncond:(self.opt.ncond+npred)].float().contiguous()
        target_costs = costs[:, self.opt.ncond:(self.opt.ncond+npred)].float().contiguous()


        if not cuda:
            input_images = input_images.cpu()
            actions = actions.cpu()
            target_images = target_images.cpu()
            
        return [input_images, input_states], actions, [target_images, target_states, target_costs]

